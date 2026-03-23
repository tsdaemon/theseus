---
name: add-service-pattern
description: Applies the established Theseus application service change workflow. Use when adding or updating a service role, wiring a service into playbooks, or enabling service vars in inventory.
---

# Add Service Pattern

Follow this fixed pattern for service work in this repository.

## When to use

Use this skill when the task is about adding or updating an application service.

## Required file touchpoints

For service `<service>`, modify only the needed files in this checklist:

1. `roles/applications/<service>/defaults/main.yml`
2. `roles/applications/<service>/tasks/main.yml`
3. `roles/applications/<service>/templates/docker-compose.yaml`
4. `inventories/local/group_vars/nas/main.yml`
5. `playbooks/theseus.yml`

## Execution rules

- Implement only explicitly requested scope.
- Keep changes minimal and consistent with nearby service patterns.
- Avoid unrelated refactors, renames, and formatting churn.
- Do not modify secret files unless explicitly requested.
- Run only targeted validation for touched files.

## Output format

After edits, report:
- Files changed
- What was changed in each file
- Any checks run
