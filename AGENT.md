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

## Working rules

- Implement only explicitly requested scope.
- Avoid unrelated refactors, renames, and formatting churn.
- Keep variable names and layout aligned with nearby service roles.
- Do not modify secrets or credentials unless explicitly asked.
- Run targeted validation only for touched files.
