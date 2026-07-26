# weisssrv.infra

The generic host-configuration roles of
[weisssrv-lib](https://git.ericsweiss.com/eric/weisssrv-lib): base hardening,
storage, DNS/SMTP, guest lifecycle, exporters and node tuning for a Proxmox +
ZFS + k3s homelab.

Site data — domains, IPs, hostnames, pool names, credentials — is an **input**
to these roles, not a default. That is the line between this collection and a
cluster instantiation: a second cluster consumes the same tag and passes its own
values.

Two documented exceptions ship a non-empty default anyway:

| Default | Why |
| --- | --- |
| `proxmox_firewall_security_groups` | Marked `EXAMPLE SET / OVERRIDE ME` where it is declared. The template's per-application group loop needs a worked example to be legible, and the shipped list keeps the rendered `cluster.fw` unchanged for the first consumer while its own groups are still in flight. It is the extension point, not a contract: a site replaces the whole list, and the role's molecule scenario asserts that a site-supplied list emits **only** the site's groups. It becomes `[]` once that consumer's values land — the `nas_storage` archive inventory was emptied the same way. |
| `nas_storage_appdata_base` (`/mnt/ssd/appdata`), `nas_storage_backup_apps_base` (`/mnt/tank/backups/apps`) | Conventional mount paths under conventional pool names, not site identity — a second cluster with the same pool layout wants exactly these. The datasets *under* them are inputs (`nas_storage_appdata_dirs`, `nas_storage_backup_artifact_apps`), and both default to empty. |

Everything else with a site-shaped value (`*_domain`, CIDR lists, pool names,
credentials) has no default and is asserted; see [MIGRATING.md](MIGRATING.md).

## Install

Pin a release tag; the collection lives in a subdirectory of the library repo,
so the path goes on the URL fragment:

```yaml
# ansible/requirements.yml
collections:
  - name: git+https://git.ericsweiss.com/eric/weisssrv-lib.git#/ansible_collections/weisssrv/infra
    type: git
    version: v0.2.0        # a release TAG; a branch works for local iteration
```

```bash
ansible-galaxy collection install -r ansible/requirements.yml
```

`galaxy.yml` declares the collections the roles actually address by FQCN
(`ansible.posix`, `community.general`), so the install pulls them too;
`requirements.yml` adds the test-only ones (`community.crypto`,
`community.docker`) that molecule needs and consumers do not.
`meta/runtime.yml` declares the ansible-core floor: **2.18**,
the line `ansible==11.6.0` ships, which is what CI tests against.

## Use

Address roles by FQCN. Nothing resolves off a local `roles/` path, which is what
makes an upgrade a one-line, reviewable ref bump:

```yaml
- hosts: nas
  roles:
    - role: weisssrv.infra.nas_storage
    - role: weisssrv.infra.zfs_encryption
```

Each role documents its variables in its own `README.md`; they are prefixed with
the role name (`nas_storage_*`), and role defaults hold only values that are
genuinely generic.

Where a value is conventionally inventory-wide and read by several roles, the
prefixed variable is **aliased** to the conventional name, so a site can set
either one. Every alias carries a `default()`, so setting only the prefixed name
never trips an undefined-variable error on the alias:

```yaml
qol_admin_user: "{{ admin_user | default('root') }}"
base_admin_user: "{{ admin_user | default('root') }}"
nas_storage_zfs_arc_max_bytes: "{{ zfs_arc_max_bytes | default('') }}"
```

The conventional names, and the roles that alias them:

| Inventory-wide name | Aliased by |
| --- | --- |
| `admin_user` | base, qol, proxmox_vm, proxmox_lxc |
| `admin_email` | base, smtp_relay |
| `ssh_port`, `ssh_permit_root_login`, `ssh_password_authentication`, `ssh_pubkey_authentication`, `ssh_authorized_keys` | base |
| `timezone` | base |
| `dns_servers` | base, proxmox_vm, proxmox_lxc |
| `internal_domain` | k3s, resolv_conf, smtp_relay, proxmox_lxc, zfs_encryption |
| `zfs_arc_max_bytes` | nas_storage, zfs_arc_cap |
| `host_dns_servers` | resolv_conf (set by base / adguard_home, not the site) |
| `vm_additional_disks` | proxmox_vm (creates + attaches), k3s (passed through to zvol_mount) |
| `nvidia_driver_version`, `nvidia_container_toolkit_version`, `nvidia_cuda_keyring_version`, `nvidia_cuda_keyring_sha256` | k3s (GPU agents; required when `k3s_gpu_node`) |

A value with no safe generic default is a **required input**: the consuming role
asserts it by name at entry rather than failing inside a template or shell
command. Each role README lists its own; [MIGRATING.md](MIGRATING.md) has the
whole set in one table.

## Migrating from un-prefixed in-tree roles

Every role variable carries its role's prefix, and every alias and default is
`| default(...)` — so a name left un-renamed does not error, it silently takes
the role default. [MIGRATING.md](MIGRATING.md) is the complete old -> new map,
per role, plus the aliased inventory-wide names that need no rename, the
externalized defaults (same name, site-specific value now empty) and the
required-input asserts that fail loudly.

## Developing against an unmerged checkout

The library repo already uses the `ansible_collections/<namespace>/<name>`
layout, so its **root is a valid collections path** — no build, no install:

```bash
ANSIBLE_COLLECTIONS_PATH=~/src/weisssrv-lib ansible-playbook site.yml
```

Prepend it to keep the installed collections (galaxy dependencies) reachable:

```bash
ANSIBLE_COLLECTIONS_PATH=~/src/weisssrv-lib:~/.ansible/collections ansible-playbook site.yml
```

`ANSIBLE_COLLECTIONS_PATHS` (plural) is the pre-2.10 alias for the same setting
and is **removed in ansible-core 2.19** — use the singular form.

## Layout

```
galaxy.yml         collection metadata + runtime dependency contract
MIGRATING.md       old -> new variable map for adopting the collection
meta/runtime.yml   requires_ansible floor
requirements.yml   galaxy deps for TEST environments (what molecule installs)
roles/<role>/      one dir per role, each with its own README + molecule scenario
plugins/           empty scaffold — see plugins/README.md before adding one
molecule-shared/   the shared molecule base config + prepare tasks
```

## Testing a role

Scenarios run from the role directory, inheriting the shared base config:

```bash
cd roles/<role>
molecule -c ../../molecule-shared/base.yml test            # default scenario
molecule -s <scenario> test                                # self-contained scenario
```

The base config wires `ANSIBLE_ROLES_PATH` to `roles/` and the collections path
to the repo root, so a scenario can reference a sibling role either bare or by
FQCN. Details and the per-scenario override rules are in
[molecule-shared/README.md](molecule-shared/README.md).

Every scenario's platform image is
`${MOLECULE_TEST_IMAGE:-…/molecule-test:latest}` — a full image ref, so a
consumer building the test image into its own registry exports
`MOLECULE_TEST_IMAGE` (tag or digest included) instead of patching each
scenario.

CI runs the same scenarios through a generated child pipeline
(`ci/internal/molecule-matrix.gitlab-ci.yml`), narrowed to the roles a merge
request actually touches.

## Linting a role

`ansible-lint` must see the repo root on the collections path, or every
`weisssrv.infra.<role>` reference in a molecule converge fails `syntax-check`:

```bash
ANSIBLE_COLLECTIONS_PATH=$PWD:~/.ansible/collections \
  ansible-lint ansible_collections/weisssrv/infra/roles/*
```

The repo-root `.ansible-lint` pins `profile: production` with **no**
`skip_list` — a role variable's name is consumer-visible API here, so
`var-naming[no-role-prefix]` stays on. `.ansible-lint-ignore` exempts that one
rule on molecule scenario playbooks only: their registers and set_facts are
test-local state, not role API.

## Versioning

One library tag versions this collection together with the CI templates,
Terraform modules, scripts and CLI — see
[docs/VERSIONING.md](../../../docs/VERSIONING.md). A renamed role, a renamed or
removed role variable, and a changed default are consumer-visible changes; a
shipped plugin is public API from its first release.
