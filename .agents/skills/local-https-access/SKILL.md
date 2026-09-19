---
name: local-https-access
description: Adds LAN-only HTTPS access (valid Let's Encrypt cert) for a Theseus service under the public domain, e.g. keycloak-admin.<domain>. Use when a service needs a secure browser context (HTTPS) on the LAN, when adding a Traefik router on the web_secure entrypoint, or when a name under the public domain must resolve only locally via Pi-hole.
---

# Local HTTPS access

Pattern: a hostname under `ansible_nas_domain` (e.g. `<svc>.<domain>`) that resolves to the NAS only inside the LAN (Pi-hole), is served by Traefik on `web_secure` (443) with a wildcard Let's Encrypt cert, and is never published through the Cloudflare Tunnel.

Use it when plain `http://<svc>.theseus` is not enough (browser features that need a secure context, e.g. Keycloak admin console/PKCE). Otherwise keep the normal `web_local` router.

## Traefik entrypoints

| Entrypoint | Listens on | Use |
|---|---|---|
| `web_local` | `:80` | LAN, plain HTTP, `<svc>.theseus` |
| `web_secure` | `:443` | LAN, HTTPS, `<svc>.<domain>`, wildcard cert (DNS-01) |
| `web_cloudflare` | `127.0.0.1:8081` | Only for the Cloudflare Tunnel (public). Never put LAN-only routers here |
| `web` | `:2881` | Legacy port-forward redirect to `web_secure`; unused |

## Steps

1. **Hostname var** in the service role defaults, e.g. `<svc>_https_hostname: "<svc>.{{ ansible_nas_domain }}"`.
2. **Traefik labels** in the service compose template (see `roles/applications/keycloak/templates/docker-compose.yaml`):
   - `traefik.http.routers.<svc>.rule: Host(...)`
   - `traefik.http.routers.<svc>.entrypoints: "web_secure"`
   - `traefik.http.routers.<svc>.tls.certresolver: "letsencrypt"`
   - `traefik.http.routers.<svc>.tls.domains[0].main: "{{ ansible_nas_domain }}"` and `...sans: "*.{{ ansible_nas_domain }}"`
   - an explicit `traefik.http.routers.<svc>.service` when the service has more than one router.
   - Homepage `homepage.href` uses the `https://` URL.
3. **Pi-hole**: add the exact hostname to `pihole_local_hostnames` in `inventories/local/group_vars/nas/main.yml`. The Pi-hole role renders `address=/<name>/<NAS IP>` plus `local=/<name>/` into `FTLCONF_misc_dnsmasq_lines`. Both are needed: `address=` alone still forwards AAAA and HTTPS queries upstream.
4. **Redeploy in order**: Traefik (`--tags traefik`; it is recreated automatically when `traefik.toml` changes), then Pi-hole (`--tags pihole`, brief DNS blip), then the service.
5. **Verify** (do not skip, each step caught a real failure):
   - `dig +short A|AAAA|HTTPS <name> @<NAS IP>`: A is the NAS IP, AAAA and HTTPS are empty.
   - `curl -sI https://<name>/` without `-k`: valid cert, HTTP/2 answer.
   - `docker --context theseus logs traefik`: no `Unable to obtain ACME certificate` errors. `Serving default certificate` means the wildcard cert was not issued.

## Certificate prerequisites

- DNS-01 must talk to the DNS host of the zone. `ansible_nas_domain` is registered at Porkbun but its nameservers are Cloudflare, so use `traefik_dns_provider: cloudflare` with `CF_DNS_API_TOKEN` (token: Zone DNS Edit + Zone Read, limited to the `<domain>` zone) in `traefik_environment_variables`. Porkbun keys fail with `CREATE_ERROR`.
- HTTP-01 and TLS-ALPN-01 cannot work here (the name is private on the LAN). Wildcards need DNS-01 anyway.
- Secrets live in `inventories/local/group_vars/nas/secrets.yaml`: never edit unless explicitly asked; tell the user which variable to set.

## Gotchas

- Cloudflare has a wildcard proxied record for `*.<domain>`, so any name without a local answer resolves to Cloudflare (SSL error 525). Every LAN-only name must be in `pihole_local_hostnames`.
- Symptoms of leaked public DNS answers: 525 from Cloudflare, `ERR_QUIC_PROTOCOL_ERROR`, Chrome's HTTPS-First "site does not support secure connection". Cause: AAAA/HTTPS (`h3`, ECH) records from Cloudflare. Fix is the Pi-hole `local=` line, not enabling HTTP/3 in Traefik. After fixing, clear Chrome's host cache or restart it, and type `https://` explicitly.
- Names in `pihole_local_hostnames` must match the router `Host(...)` exactly.
- `FTLCONF_*` env vars make the setting read-only in the Pi-hole UI; the repo is the source of truth. Do not add these names by hand in Pi-hole.
- `traefik.toml` is a bind mount; without the forced recreate, entrypoint changes are not picked up.
- Keycloak specific: `KC_HOSTNAME_ADMIN` alone does not change the master realm issuer. Set the master realm Frontend URL to the admin URL with `kcadm.sh update realms/master -s attributes.frontendUrl=https://<admin host>`, otherwise the admin console times out on the 3rd-party iframe check (the iframe is loaded from the blocked public URL).

## Public access is a different pattern

Public services go through the Cloudflare Tunnel to `127.0.0.1:8081` (`web_cloudflare`). The router path allowlist lives in Traefik labels (see the Keycloak `keycloak-public` router). Do not mix the two: a LAN-only router must never use `web_cloudflare`.
