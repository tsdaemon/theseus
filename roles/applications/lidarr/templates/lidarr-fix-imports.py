#!/usr/bin/env python3
# {{ ansible_managed }}
"""Clear Lidarr queue items that Lidarr can't import by itself.

Stuck queue items (importFailed / importBlocked, including unknown-artist ones):
- Image (a CUE image, or any release with a single .flac) and archive (only
  .zip/.rar/.7z, no audio): remove from queue and download client, blocklist
  the release, and let Lidarr search for another one.
- Mixed FLAC + MP3 release: hardlink only the FLAC files into a scratch
  folder, let Lidarr match that, and import it against the original download.
  The torrent's own files are untouched, so it keeps seeding.
- Anything else ("other"): import what Lidarr matched if the album match is at
  least --min-match % (Lidarr's own bar is 80 %).
Imports skip files Lidarr couldn't match to a track, ignore "missing" and
"unmatched tracks", and skip the whole download on any other rejection
(destination exists, not an upgrade, bad track match, ...).

--orphans: torrents in qBittorrent's "lidarr" category that Lidarr no longer
tracks. Images and archives among them were never imported, so they're
deleted together with their files, unless another torrent shares their
folder. Other orphans are left alone.

Dry run by default; pass --apply to act.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request

LIDARR_URL = "http://localhost:{{ lidarr_port }}/api/v1"
LIDARR_CONFIG = "{{ lidarr_config_directory }}/config.xml"
# qBittorrent Web UI must allow unauthenticated access from localhost.
QBIT_URL = "http://localhost:{{ qbittorrent_port_http }}/api/v2"
QBIT_CATEGORY = "lidarr"
# Lidarr and qBittorrent both see HOST_ROOT as CONTAINER_ROOT.
HOST_ROOT = "{{ lidarr_downloads_directory }}"
CONTAINER_ROOT = "/downloads"
SCRATCH_DIR = os.path.join(HOST_ROOT, "media", ".lidarr-fix-imports")

AUDIO_EXTS = {".flac", ".mp3", ".ape", ".m4a", ".wv", ".ogg", ".opus", ".wav", ".aiff", ".dsf"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z"}
STUCK_STATES = {"importFailed", "importBlocked"}
# Kinds removed + blocklisted from the queue, or deleted as orphans.
JUNK_KINDS = ("image", "archive")
ALBUM_MATCH_RE = re.compile(r"Album match is not close enough: ([\d.]+) %")
# Rejections that don't stop a manual import of the matched files.
SOFT_REJECTIONS = ("Has missing tracks", "Has unmatched tracks")


def api_key():
    with open(LIDARR_CONFIG, encoding="utf-8") as f:
        return re.search(r"<ApiKey>([^<]+)</ApiKey>", f.read()).group(1)


def http(method, url, body=None, headers=None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


class Lidarr:
    def __init__(self):
        self.key = api_key()

    def call(self, method, path, params=None, body=None):
        url = f"{LIDARR_URL}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        return http(method, url, data, {"X-Api-Key": self.key, "Content-Type": "application/json"})

    def queue(self):
        # Unknown-artist items (release name Lidarr couldn't parse) are shown
        # in the UI but left out of the API unless asked for.
        records, page = [], 1
        while True:
            res = self.call("GET", "queue", {
                "page": page, "pageSize": 500, "includeUnknownArtistItems": "true",
            })
            records += res["records"]
            if len(records) >= res["totalRecords"] or not res["records"]:
                return records
            page += 1


def to_host(path):
    return HOST_ROOT + path[len(CONTAINER_ROOT):] if path.startswith(CONTAINER_ROOT) else path


def to_container(path):
    return CONTAINER_ROOT + path[len(HOST_ROOT):] if path.startswith(HOST_ROOT) else path


def scan(host_path):
    """Return (audio, cues, archives) file paths under host_path."""
    paths = [host_path] if os.path.isfile(host_path) else [
        os.path.join(root, name) for root, _, files in os.walk(host_path) for name in files]
    audio, cues, archives = [], [], []
    for path in paths:
        ext = os.path.splitext(path)[1].lower()
        if ext in AUDIO_EXTS:
            audio.append(path)
        elif ext == ".cue":
            cues.append(path)
        elif ext in ARCHIVE_EXTS:
            archives.append(path)
    return audio, cues, archives


def classify(host_path):
    if not host_path or not os.path.exists(host_path):
        return "missing"
    audio, cues, archives = scan(host_path)
    exts = {os.path.splitext(f)[1].lower() for f in audio}
    flacs = [f for f in audio if f.lower().endswith(".flac")]
    if len(flacs) == 1 or (cues and audio and len(audio) <= len(cues)):
        return "image"
    if archives and not audio:
        return "archive"
    if {".flac", ".mp3"} <= exts:
        return "mixed"
    return "other"


def remove_and_research(lidarr, kind, downloads, apply):
    ids = [r["id"] for recs in downloads for r in recs]
    for recs in downloads:
        print(f"  {kind.upper():7} {recs[0]['title']}")
    if not apply or not ids:
        return
    for i in range(0, len(ids), 50):
        lidarr.call("DELETE", "queue/bulk",
                    {"removeFromClient": "true", "blocklist": "true", "skipRedownload": "false"},
                    {"ids": ids[i:i + 50]})
    print(f"  removed + blocklisted {len(downloads)} {kind} downloads; Lidarr will search again")


def wait_for(lidarr, command_id, timeout=600):
    deadline = time.time() + timeout
    while time.time() < deadline:
        cmd = lidarr.call("GET", f"command/{command_id}")
        if cmd["status"] in ("completed", "failed", "aborted", "cancelled"):
            return cmd["status"]
        time.sleep(2)
    return "timeout"


def blocking(reason, min_match):
    m = ALBUM_MATCH_RE.search(reason)
    if m:
        return float(m.group(1)) < min_match
    return not reason.startswith(SOFT_REJECTIONS)


def import_download(lidarr, kind, recs, args):
    """Import what Lidarr can match. For mixed releases only the FLAC files are
    offered, via hardlinks in a scratch folder."""
    rec = recs[0]
    download_id = rec["downloadId"]
    source = to_host(rec["outputPath"])
    print(f"  {kind.upper():7} {rec['title']}")

    scratch = None
    if kind == "mixed":
        scratch = os.path.join(SCRATCH_DIR, download_id)
        shutil.rmtree(scratch, ignore_errors=True)
        audio, _, _ = scan(source)
        for path in audio:
            if os.path.splitext(path)[1].lower() != ".flac":
                continue
            dest = os.path.join(scratch, os.path.relpath(path, source))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            os.link(path, dest)
        # No downloadId here: with it, Lidarr scans the download's own folder
        # and ignores `folder`. It's attached to the import command instead.
        params = {"folder": to_container(scratch), "filterExistingFiles": "false"}
    else:
        params = {"folder": rec["outputPath"], "downloadId": download_id, "filterExistingFiles": "false"}

    try:
        items = lidarr.call("GET", "manualimport", params)
        matched = [it for it in items if it.get("tracks")]
        problems = sorted({f"{it['name']}: {rej['reason']}" for it in matched
                           for rej in it.get("rejections", [])
                           if blocking(rej["reason"], args.min_match)})
        if not matched:
            problems.append("no file matched a track")
        if problems:
            print(f"          skipped, Lidarr would reject it:")
            for p in problems[:5]:
                print(f"            - {p}")
            return
        match = min((float(m.group(1)) for it in matched for rej in it.get("rejections", [])
                     for m in [ALBUM_MATCH_RE.search(rej["reason"])] if m), default=None)
        print(f"          {len(matched)}/{len(items)} files match"
              + (f" (album match {match} %)" if match is not None else ""))
        if not args.apply:
            return
        files = [{
            "path": it["path"],
            "artistId": it["artist"]["id"],
            "albumId": it["tracks"][0]["albumId"],
            "albumReleaseId": it["albumReleaseId"],
            "trackIds": [t["id"] for t in it["tracks"]],
            "quality": it["quality"],
            "indexerFlags": it.get("indexerFlags", 0),
            "downloadId": download_id,
            "disableReleaseSwitching": it.get("disableReleaseSwitching", False),
        } for it in matched]
        cmd = lidarr.call("POST", "command", body={
            "name": "ManualImport",
            "files": files,
            # Scratch hardlinks can be moved; the torrent's own files are left
            # to Lidarr (hardlink/copy while seeding).
            "importMode": "move" if scratch else "auto",
            "replaceExistingFiles": False,
        })
        print(f"          import {wait_for(lidarr, cmd['id'])}")
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)


def fix_queue(lidarr, records, args):
    downloads = {}
    for rec in records:
        if rec.get("downloadId"):
            downloads.setdefault(rec["downloadId"], []).append(rec)
    stuck = [recs for recs in downloads.values()
             if any(r.get("trackedDownloadState") in STUCK_STATES for r in recs)]

    kinds = {"image": [], "archive": [], "mixed": [], "other": [], "missing": []}
    for recs in stuck:
        kinds[classify(to_host(recs[0].get("outputPath") or ""))].append(recs)
    print(f"stuck queue downloads: {len(stuck)} "
          + ", ".join(f"{k}={len(v)}" for k, v in kinds.items()))

    for kind in JUNK_KINDS:
        if args.only in (None, kind):
            remove_and_research(lidarr, kind, kinds[kind][:args.limit], args.apply)
    for kind in ("mixed", "other"):
        if args.only not in (None, kind):
            continue
        for recs in kinds[kind][:args.limit]:
            try:
                import_download(lidarr, kind, recs, args)
            except Exception as e:  # keep going with the rest
                print(f"          error: {e}")


def fix_orphans(records, args):
    tracked = {r["downloadId"].lower() for r in records if r.get("downloadId")}
    torrents = http("GET", f"{QBIT_URL}/torrents/info?category={QBIT_CATEGORY}") or []
    orphans = [t for t in torrents if t["hash"].lower() not in tracked]
    # Several torrents can save into the same folder; deleting one "with
    # files" would wipe the others' files too.
    folder_users = {}
    for t in http("GET", f"{QBIT_URL}/torrents/info") or []:
        folder_users[t["content_path"]] = folder_users.get(t["content_path"], 0) + 1
    shared = [t for t in orphans if folder_users[t["content_path"]] > 1]
    orphans = [t for t in orphans if folder_users[t["content_path"]] == 1]

    junk = []
    for t in orphans:
        kind = classify(to_host(t["content_path"]))
        if kind in JUNK_KINDS and args.only in (None, kind):
            junk.append((kind, t))
    junk = junk[:args.limit]
    size = sum(t["size"] for _, t in junk)
    print(f"orphan torrents: {len(orphans) + len(shared)} ({len(shared)} share a folder, kept), "
          f"deletable images/archives: {len(junk)} "
          f"({size / 1e9:.1f} GB)")
    for kind, t in junk:
        print(f"  {kind.upper():7} {t['name']}")
    if not args.apply or not junk:
        return
    hashes = [t["hash"] for _, t in junk]
    for i in range(0, len(hashes), 50):
        body = urllib.parse.urlencode({"hashes": "|".join(hashes[i:i + 50]), "deleteFiles": "true"})
        http("POST", f"{QBIT_URL}/torrents/delete", body.encode(),
             {"Content-Type": "application/x-www-form-urlencoded"})
    print(f"  deleted {len(junk)} torrents with their files")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="act instead of just reporting")
    parser.add_argument("--orphans", action="store_true",
                        help="clean orphan torrents instead of the Lidarr queue")
    parser.add_argument("--only", choices=["image", "archive", "mixed", "other"], help="handle only one kind")
    parser.add_argument("--limit", type=int, help="handle at most N downloads per kind")
    parser.add_argument("--min-match", type=float, default=70,
                        help="lowest album match %% to import (default 70; Lidarr uses 80)")
    args = parser.parse_args()

    lidarr = Lidarr()
    records = lidarr.queue()
    if not args.apply:
        print("dry run; pass --apply to act")
    if args.orphans:
        fix_orphans(records, args)
    else:
        fix_queue(lidarr, records, args)


if __name__ == "__main__":
    sys.exit(main())
