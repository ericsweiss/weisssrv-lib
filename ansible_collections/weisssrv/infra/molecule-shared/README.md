# molecule-shared

Shared Molecule scaffolding for the collection's role scenarios: one base
config, one prepare playbook, and the task files scenarios import directly.

| File | Purpose |
|---|---|
| `base.yml` | deep-merged base config (`molecule -c .../molecule-shared/base.yml test`) — driver, provisioner env/config, test sequence |
| `prepare-common.yml` | default `provisioner.playbooks.prepare` for every scenario |
| `tasks/prepare-base.yml` | wait for systemd + ensure the ansible temp dir |
| `tasks/prepare-apt-disable.yml` | stop/disable the apt background timers (dpkg-lock races) |
| `tasks/container-warmup.yml` | wait out apt locks, fix interrupted dpkg, refresh the cache |

## Layout the relative paths assume

```
<repo-root>/                           # = the collections path (holds ansible_collections/)
  ansible_collections/weisssrv/infra/  # <collection-root>
    requirements.yml                   # galaxy deps (role-file/requirements-file)
    molecule-shared/                   # this dir
    roles/<role>/molecule/<scenario>/  # molecule.yml, converge.yml, verify.yml
```

`base.yml` resolves `role-file`/`requirements-file` against molecule's CWD (the
role dir), and `playbooks.prepare` plus the `ANSIBLE_ROLES_PATH` /
`ANSIBLE_COLLECTIONS_PATH` env against the scenario dir — three different
depths, so all three are wrong if the tree is reshaped. A role scenario
overrides only what it needs; unset keys inherit from here.

`ANSIBLE_COLLECTIONS_PATH` is what lets a scenario address content as
`weisssrv.infra.<name>`: molecule sets no collections path itself, so without it
only bare role names (via `ANSIBLE_ROLES_PATH`) resolve.

## Using it from a scenario

```yaml
# roles/<role>/molecule/default/prepare.yml (only when the role needs extra prep)
- name: Prepare
  hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.import_tasks: ../../../../molecule-shared/tasks/prepare-base.yml
    - ansible.builtin.import_tasks: ../../../../molecule-shared/tasks/prepare-apt-disable.yml
```

A scenario that ships its own `prepare.yml` must also re-point
`provisioner.playbooks.prepare` at it in its `molecule.yml` (an unset key
inherits `prepare-common.yml`).

## Always invoke from the role directory

`cd roles/<role> && molecule ...` — that is what the relative paths above are
resolved against, and it is what CI does. Running molecule from the collection
root instead finds `galaxy.yml`, switches to the collection scenario layout
(`extensions/molecule/*/molecule.yml`) and reports **no scenarios**, which reads
as "nothing to test" rather than as an error.
