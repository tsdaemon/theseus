# Theseus

Theseus is a personal infrastructure project derived from [Ansible-NAS](https://github.com/davestephens/ansible-nas), then heavily customized for one operator and one environment.

This repository keeps the familiar Ansible-NAS structure, but the behavior and service set are intentionally tailored to specific needs instead of trying to remain a generic distribution.

## Project intent

- Manage a self-hosted NAS and application stack with Ansible.
- Keep service configuration reproducible and versioned.
- Optimize for practical, personal workflows over broad compatibility.

## Repository structure

- `playbooks/`: top-level orchestration entrypoints.
- `roles/`: reusable service and infrastructure roles.
- `inventories/`: environment-specific variables and host configuration.
- Root config files: linting, CI, and local task automation.

## `roles/` folder structure

Top-level role groups in this repository:

- `roles/applications/`: end-user application services (for example `gramps`, `plex`, `immich`).
- `roles/services/`: shared platform services used by apps.
- `roles/configuration/`: host/system configuration roles.
- `roles/storage/`: storage-related setup.
- `roles/gpu/`: GPU-specific setup.
- `roles/o11y/`: observability and monitoring-related roles.

Application roles usually follow this internal layout:

- `defaults/main.yml`: service variables and defaults.
- `tasks/main.yml`: lifecycle tasks (directories, templates, compose up/down).
- `templates/docker-compose.yaml`: compose definition rendered by Ansible.

## Customization model

Most changes in this repository are incremental service-level updates. Typical service work consists of:

1. Defining role defaults.
2. Implementing role tasks.
3. Maintaining docker-compose templates for that role.
4. Wiring inventory variables.
5. Registering the role in the main playbook.

This pattern keeps behavior consistent while allowing each service to be customized deeply where needed.

## Example service definition

Example: `roles/applications/gramps/`

`defaults/main.yml` defines service defaults:

```yaml
gramps_enabled: false
gramps_available_externally: false
gramps_directory: "{{ docker_home }}/gramps"
gramps_image_name: "ghcr.io/gramps-project/grampsweb"
gramps_image_version: "latest"
gramps_port: "5000"
```

`tasks/main.yml` creates directories, renders compose, and controls service state:

```yaml
- name: Start Gramps
  block:
    - name: Create Gramps Directories
      ansible.builtin.file:
        path: "{{ item }}"
        state: directory
    - name: Create docker-compose.yaml
      ansible.builtin.template:
        src: docker-compose.yaml
        dest: "{{ gramps_directory }}/docker-compose.yaml"
    - name: Start Gramps
      community.docker.docker_compose_v2:
        project_src: "{{ gramps_directory }}"
        state: present
  when: gramps_enabled
```

`templates/docker-compose.yaml` contains the runtime service model:

```yaml
services:
  gramps:
    image: "{{ gramps_image_name }}:{{ gramps_image_version }}"
    ports:
      - "{{ gramps_port }}:5000"
```
