#!/usr/bin/env python3
# {{ ansible_managed }}
"""Clear Lidarr queue items that Lidarr can't import by itself.

Queue items, including unknown-artist ones:
- Complete: stuck or still downloading, but every album the download is for
  already has all its tracks at or above the quality profile's cutoff (e.g.
  filled by another release or by a rescan). Remove from queue and download
  client, no blocklist, no new search. Lidarr never does this by itself.
- Removed: no artist and no grab history, i.e. the artist was removed from
  Lidarr (which deletes its history but leaves queue items behind as "Artist
  name mismatch"), and none of its audio is in the library. Remove from queue
  and download client, no blocklist, no new search.
- Stalled: still downloading, but qBittorrent has seen no activity for
  --stalled-days days (dead or metadata-less torrent). Remove, blocklist the
  release, and let Lidarr search for another one.

Stuck queue items (importFailed / importBlocked):
- Image (a CUE image, or any release with a single .flac) and archive (only
  .zip/.rar/.7z or video files like .avi, no audio): remove from queue and download client, blocklist
  the release, and let Lidarr search for another one.
- Mixed FLAC + MP3 release: hardlink only the FLAC files into a scratch
  folder, let Lidarr match that, and import it against the original download.
  The torrent's own files are untouched, so it keeps seeding.
- Anything else ("other"): import what Lidarr matched if the album match is at
  least --min-match % (Lidarr's own bar is 80 %).
Imports skip files Lidarr couldn't match to a track, ignore "missing" and
"unmatched tracks", and skip the whole download on any other rejection
(destination exists, not an upgrade, bad track match, ...).

Orphans: torrents in qBittorrent's "lidarr" category that Lidarr no longer
tracks (artist removed, album filled elsewhere, never imported). Deleted with
their files when none of their audio is hardlinked into the library and no
other torrent shares their folder.

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
# Not importable by Lidarr; a release with only these is treated as "archive".
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".avi", ".mkv", ".mp4", ".m4v", ".mov", ".wmv", ".vob"}
STUCK_STATES = {"importFailed", "importBlocked"}
# qBittorrent states of a torrent that isn't getting data.
STALLED_QBIT_STATES = {"stalledDL", "metaDL", "forcedMetaDL"}
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

    def cutoff_unmet(self):
        """Ids of albums whose files are below the quality profile's cutoff."""
        ids, page = set(), 1
        while True:
            res = self.call("GET", "wanted/cutoff", {"page": page, "pageSize": 1000})
            ids.update(a["id"] for a in res["records"])
            if page * 1000 >= res["totalRecords"] or not res["records"]:
                return ids
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


def album_complete(lidarr, album_ids, cache, cutoff_unmet):
    for album_id in album_ids:
        if album_id in cutoff_unmet:
            return False
        if album_id not in cache:
            stats = lidarr.call("GET", f"album/{album_id}").get("statistics", {})
            cache[album_id] = stats.get("trackCount", 0) > 0 and \
                stats.get("trackFileCount", 0) >= stats["trackCount"]
        if not cache[album_id]:
            return False
    return True


def artist_removed(lidarr, recs):
    if any(r.get("artistId") for r in recs):
        return False
    history = lidarr.call("GET", "history", {"downloadId": recs[0]["downloadId"], "pageSize": 10})
    return not any(h["eventType"] == "grabbed" for h in history["records"])


def remove_done(lidarr, kind, downloads, apply):
    ids = [r["id"] for recs in downloads for r in recs]
    for recs in downloads:
        print(f"  {kind.upper():8} {recs[0]['title']}")
    if not apply or not ids:
        return
    for i in range(0, len(ids), 50):
        lidarr.call("DELETE", "queue/bulk",
                    {"removeFromClient": "true", "blocklist": "false", "skipRedownload": "true"},
                    {"ids": ids[i:i + 50]})
    print(f"  removed {len(downloads)} {kind} downloads")


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
    torrents = {t["hash"].lower(): t
                for t in http("GET", f"{QBIT_URL}/torrents/info?category={QBIT_CATEGORY}") or []}
    stalled_before = time.time() - args.stalled_days * 86400
    cutoff_unmet = lidarr.cutoff_unmet()

    kinds = {"complete": [], "removed": [], "stalled": [], "image": [], "archive": [], "mixed": [],
             "other": [], "missing": []}
    cache = {}
    for download_id, recs in downloads.items():
        states = {r.get("trackedDownloadState") for r in recs}
        stuck = bool(states & STUCK_STATES)
        if not stuck and "downloading" not in states:
            continue
        album_ids = {r["albumId"] for r in recs if r.get("albumId")}
        if album_ids and album_complete(lidarr, album_ids, cache, cutoff_unmet):
            kinds["complete"].append(recs)
        elif stuck and artist_removed(lidarr, recs):
            if not in_library(to_host(recs[0].get("outputPath") or "")):
                kinds["removed"].append(recs)
        elif stuck:
            kinds[classify(to_host(recs[0].get("outputPath") or ""))].append(recs)
        else:
            t = torrents.get(download_id.lower())
            if t and t["state"] in STALLED_QBIT_STATES and \
                    max(t["last_activity"], t["added_on"]) < stalled_before:
                kinds["stalled"].append(recs)
    print("queue downloads to handle: " + ", ".join(f"{k}={len(v)}" for k, v in kinds.items()))

    if args.only in (None, "complete"):
        remove_done(lidarr, "complete", kinds["complete"][:args.limit], args.apply)
    if args.only in (None, "removed"):
        remove_done(lidarr, "removed", kinds["removed"][:args.limit], args.apply)
    if args.only in (None, "stalled"):
        remove_and_research(lidarr, "stalled", kinds["stalled"][:args.limit], args.apply)
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


def in_library(host_path):
    """True if any audio file of the download is hardlinked elsewhere, i.e.
    Lidarr imported it and the library still uses it."""
    audio, _, _ = scan(host_path)
    return any(os.stat(f).st_nlink > 1 for f in audio)


def fix_orphans(records, args):
    tracked = {r["downloadId"].lower() for r in records if r.get("downloadId")}
    all_torrents = http("GET", f"{QBIT_URL}/torrents/info") or []
    # Several torrents can save into the same folder; deleting one "with
    # files" would wipe the others' files too.
    folder_users = {}
    for t in all_torrents:
        folder_users[t["content_path"]] = folder_users.get(t["content_path"], 0) + 1

    orphans = [t for t in all_torrents
               if t["category"] == QBIT_CATEGORY and t["hash"].lower() not in tracked]
    shared = [t for t in orphans if folder_users[t["content_path"]] > 1]
    used = [t for t in orphans
            if folder_users[t["content_path"]] == 1 and in_library(to_host(t["content_path"]))]
    unused = [t for t in orphans if t not in shared and t not in used][:args.limit]

    size = sum(t["size"] for t in unused)
    print(f"orphan torrents: {len(orphans)}; kept: {len(used)} with files in the library, "
          f"{len(shared)} sharing a folder; deletable: {len(unused)} ({size / 1e9:.1f} GB)")
    for t in unused:
        print(f"  ORPHAN  {t['name']}")
    if not args.apply or not unused:
        return
    hashes = [t["hash"] for t in unused]
    for i in range(0, len(hashes), 50):
        body = urllib.parse.urlencode({"hashes": "|".join(hashes[i:i + 50]), "deleteFiles": "true"})
        http("POST", f"{QBIT_URL}/torrents/delete", body.encode(),
             {"Content-Type": "application/x-www-form-urlencoded"})
    print(f"  deleted {len(unused)} torrents with their files")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="act instead of just reporting")
    parser.add_argument("--only", choices=["complete", "removed", "stalled", "image", "archive", "mixed", "other", "orphans"], help="handle only one kind")
    parser.add_argument("--limit", type=int, help="handle at most N downloads per kind")
    parser.add_argument("--min-match", type=float, default=70,
                        help="lowest album match %% to import (default 70; Lidarr uses 80)")
    parser.add_argument("--stalled-days", type=float, default=7,
                        help="days without qBittorrent activity before a download counts as stalled")
    args = parser.parse_args()

    lidarr = Lidarr()
    records = lidarr.queue()
    if not args.apply:
        print("dry run; pass --apply to act")
    if args.only != "orphans":
        fix_queue(lidarr, records, args)
    if args.only in (None, "orphans"):
        # Runs on the queue as it was before this run's removals; those
        # torrents are gone from qBittorrent already.
        fix_orphans(records, args)


if __name__ == "__main__":
    sys.exit(main())
