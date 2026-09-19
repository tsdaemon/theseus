# Agent Instructions for Theseus

Theseus is a personal fork of Ansible-NAS. Prioritize existing local patterns over generic upstream conventions.

## What to optimize for

- Practical fit for this specific environment.
- Minimal, targeted changes.
- Consistency with neighboring roles and inventory settings.

## Core structure

- `roles/`: role implementation, including service defaults/tasks/templates.
- `playbooks/theseus.yml`: main role wiring and tags.
- `inventories/local/group_vars/nas/main.yml`: local service enablement and vars.
- `inventories/local/group_vars/nas/secrets.yaml`: secrets (edit only if explicitly requested).

## Service change checklist

When adding/updating service `<service>`, touch only what is needed:
1. `roles/applications/<service>/defaults/main.yml`
2. `roles/applications/<service>/tasks/main.yml`
3. `roles/applications/<service>/templates/docker-compose.yaml`
4. `inventories/local/group_vars/nas/main.yml`
5. `playbooks/theseus.yml`

## Traefik entrypoints

- `web_local` (`:80`): LAN plain HTTP, `<svc>.theseus`.
- `web_secure` (`:443`): LAN HTTPS with a wildcard Let's Encrypt cert for the public domain (DNS-01 via Cloudflare). Names must be listed in `pihole_local_hostnames`.
- `web_cloudflare` (`127.0.0.1:8081`): only for services published through the Cloudflare Tunnel. Never attach LAN-only routers to it.
- LAN HTTPS for a service: follow `.agents/skills/local-https-access/SKILL.md`.
- Nothing is published on the router or forwarded; there is no external port forward anymore.

## Working rules

- Implement only explicitly requested scope.
- Avoid unrelated refactors, renames, and formatting churn.
- Keep variable names and layout aligned with nearby service roles.
- Do not modify secrets or credentials unless explicitly asked.
- Run targeted validation only for touched files.

## Digital Home inventory (Notion)

Source: https://app.notion.com/p/9943ed165e264e9181053ca8a0b71efe (hub: "Digital Home" under "Дім"). It is the inventory of the home's digital elements: what lives where, how it is connected, dependencies, and what needs maintenance. The schema may be incomplete or outdated; ask the user before acting on discrepancies. Theseus (the NAS) is one of its Hardware records.

Read that page before any service change. It applies to every new or modified service in this repo:

- Service enabled (`<service>_enabled: true`): make sure it has a Software record; add one if missing.
- Service disabled but already in the inventory: keep the record and mark it as disabled (do not delete it).
- Link the Repos record if one exists for the service (create it per the rules below if not); use the service's original icon on the record.
- Adding or changing a service is not finished until the inventory matches.

### Modeling

- Separate tables, never merged: Hardware, Software, Systems, Signals, Maintenance, Repos, Projects, Tasks.
- Systems are logical "circuits" (not an enum); a device/service belongs to one system.
- Repos is its own table, linked from Hardware and Software via the `Repos` relation. Do not use the legacy `Repo` URL field.
- Maintenance links to Hardware and has its own table and board views.
- Tasks (`Status`, `Due`, `Project`, `System`, `Hardware`, `Software`) is the planning database; Projects holds big initiatives, with context/design on the project page and tasks linked via `Project`.
- Signals link to Software via `From (software)`.
- Hardware fields: `IP`, `Hostname`, `Power`, `Powered by`, `Gateway`, `Connectivity`, `Depends on (software)`, `Location`, `Criticality`, `System`, `Repos`.
- Software fields: `Runtime` (Local/Cloud/Hybrid), `Runs on`, `Service URL`, `Endpoint`, `Criticality`, `System`, `Repos`.

### Rules when updating it

- Ask for confirmation before changing a database schema.
- New repository: add a Repos record and link it via `Repos` on Hardware/Software.
- New device: set `System`, `Location`, `Power`, `Powered by`, `Connectivity`, `Gateway`, `Criticality`.
- New service: set `Runtime`, `Runs on`, `Service URL`/`Endpoint`, `Criticality`, `System`, `Repos`.
- Maintenance activities: Maintenance record linked to Hardware, with `Frequency`, `Urgency`, `Next date`, and a checklist in `Checklist / Notes`.
- Project tasks go in Tasks with the `Project` relation, not as a checklist on the project page or in Maintenance.
- Hardware/Software installed as part of a project is still added to the main inventory.
- To save session progress, add a progress page at the bottom of the Notion AGENTS.md page.

### Style

- Keep the model simple; add detail in small increments.
- Short Ukrainian notes in `Notes`; details on the device/service page.
- Photos and screenshots go on the device page.
