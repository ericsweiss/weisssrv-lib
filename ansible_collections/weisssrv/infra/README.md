# weisssrv.infra

The host-configuration roles of
[weisssrv-lib](https://git.ericsweiss.com/eric/weisssrv-lib): base hardening,
storage, DNS/SMTP, guest lifecycle, exporters, node tuning, and the application
guests a Proxmox + ZFS + k3s homelab runs.

Site data — domains, IPs, hostnames, pool names, credentials — is an **input**
to these roles, not a default. That is the line between this collection and a
cluster instantiation: a second cluster consumes the same tag and passes its own
values. A value with no safe generic default is asserted by name at role entry,
so a missed rename fails the play instead of rendering an empty string.

One documented exception ships a non-empty default anyway:

| Default | Why |
| --- | --- |
| `nas_storage_appdata_base` (`/mnt/ssd/appdata`), `nas_storage_backup_apps_base` (`/mnt/tank/backups/apps`) | Conventional mount paths under conventional pool names, not site identity — a second cluster with the same pool layout wants exactly these. The datasets *under* them are inputs (`nas_storage_appdata_dirs`, `nas_storage_backup_artifact_apps`), and both default to empty. |

## Install

Pin a release tag; the collection lives in a subdirectory of the library repo,
so the path goes on the URL fragment:

```yaml
# ansible/requirements.yml
collections:
  - name: git+https://git.ericsweiss.com/eric/weisssrv-lib.git#/ansible_collections/weisssrv/infra
    type: git
    version: <CURRENT_TAG>     # a release TAG; a branch works for local iteration
```

```bash
ansible-galaxy collection install -r ansible/requirements.yml
```

`<CURRENT_TAG>` is the placeholder convention used across this repo's examples;
the current release is named in the library
[README](../../../README.md#current-release).

`galaxy.yml` declares the collections the roles actually address by FQCN
(`ansible.posix`, `community.general`), so the install pulls them too;
`requirements.yml` adds the test-only ones (`community.crypto`,
`community.docker`) that molecule needs and consumers do not.
`meta/runtime.yml` declares the ansible-core floor: **2.18**, which is what
`ansible==11.6.0` ships and what CI tests against.

## Roles

40 roles, addressed by FQCN. Each has its own `README.md` (variables, task flow,
operator notes) and its own molecule scenario. "New" marks a role first shipped
in this release; everything else has been in the collection since 0.2.0.

### Host baseline and tuning

| Role | Purpose | New |
| --- | --- | :-: |
| `base` | Packages, SSH hardening, sudoers, timezone, DNS, fail2ban, unattended-upgrades | |
| `qol` | Shell/editor conveniences: zsh, Oh My Zsh, Neovim + plugins | |
| `apt_signed_repo` | Shared pipeline for adding a fingerprint-verified signed APT repository | |
| `resolv_conf` | Managed `/etc/resolv.conf`, with optional immutable-flag handling | |
| `nic_tuning` | Per-NIC ethtool offload overrides, bond ASA guard, `ip_forward` sysctl, offload read-back gate | |
| `encrypted_swap` | dm-crypt plain-mode random-key swap (crypttab + fstab) for bare metal | |
| `tailscale` | Tailscale with subnet routing and a pinned, fully-verified signing key | |

### Proxmox host and guest lifecycle

| Role | Purpose | New |
| --- | --- | :-: |
| `proxmox_vm` | VM provisioning: cloud-init or Windows, persistent ZFS zvols, memory/CPU reconcile | |
| `proxmox_lxc` | LXC provisioning: bind mounts, GPU passthrough, UID mapping | |
| `proxmox_firewall` | Cluster/host/guest firewall, IPSets, security groups, pveum users and tokens | |
| `proxmox_ha` | HA rules and resources plus ZFS replication jobs, reconciled from one config index | |
| `proxmox_backup` | Declarative storage entries and vzdump jobs via `pvesh` | |
| `vfio_passthrough` | GPU VFIO host codification (IOMMU cmdline, vfio-pci bind, driver blacklist); stages config and flags reboot-required, never reboots | |
| `zvol_mount` | Mounts attached zvols by stable device path with UUID-based fstab entries | |

### Storage

| Role | Purpose | New |
| --- | --- | :-: |
| `nas_storage` | ZFS properties and scrubs, NFS + Samba exports, mergerfs, media-mover, SMART, archive replication, backup-artifact metrics, `/etc/pve` cluster-config archive | |
| `zfs_encryption` | Boot-time unlock of ZFS-native encrypted pools from 1Password Connect, with the mount/guest-start ordering units | |
| `zfs_arc_cap` | Caps the ZFS ARC on compute hosts (modprobe.d + initramfs + live sysfs) | |
| `nfs_tls` | tlshd (ktls-utils) for NFSv4 transport security (`xprtsec=tls`/`mtls`) | |
| `restic_offsite` | Nightly offsite backup to Backblaze B2 (restic + rclone): retention ceiling, stale-lock reaper, rotating deep verify | |

### DNS, mail and certificates

| Role | Purpose | New |
| --- | --- | :-: |
| `unbound` | Forwarding resolver, DoT to public upstreams, managed drop-in | |
| `adguard_home` | AdGuard Home with API-driven config and an atomic admin-password reconcile | |
| `adguard_sync` | Replicates AdGuard settings primary → replica, with freshness metrics | |
| `smtp_relay` | Central Postfix relay to an upstream provider with SASL, as a merge over role defaults | |
| `postfix_null_client` | Postfix satellite forwarding to the relay, incl. compiled-map repair | |
| `acme_certs` | Wildcard certificates via acme.sh (DNS-01) plus verified push to each consuming host | |

### Kubernetes

| Role | Purpose | New |
| --- | --- | :-: |
| `k3s` | k3s servers/agents, kube-vip API VIP, TLS SANs, GPU agents, off-node etcd snapshots, optional metrics-server override | |

### Observability

| Role | Purpose | New |
| --- | --- | :-: |
| `prometheus_exporter` | Shared download → install → enable → health pipeline for tarball and `.deb` exporters | |
| `node_exporter_host` | node_exporter on bare metal, textfile collectors (corosync, zpool, smartmon, vzdump) and a liveness gate | |
| `textfile_collector` | Shared oneshot-service + timer scaffold for any textfile collector | |
| `zfs_exporter` | ZFS pool/dataset exporter (`prometheus_exporter` wrapper) | |
| `unbound_exporter` | Unbound exporter (`prometheus_exporter` wrapper) | |
| `alloy_host` | Grafana Alloy shipping journald to Loki, with the stream-cardinality relabel contract | |

### Application guests

| Role | Purpose | New |
| --- | --- | :-: |
| `docker_engine` | Pinned, dpkg-held Docker CE + plugins and a journald `daemon.json` | |
| `compose_app` | Shared scaffolding for a single-project compose guest: unit template, backup-metrics library, validated host-nginx TLS terminator | |
| `gitlab` | GitLab EE (Omnibus): TLS, registry, Pages, SMTP, SAML, backups with secrets-presence metrics, Git-SSH hardening | ● |
| `plex` | Plex Media Server: GPU transcoding, media group, custom-certificate reload hook | ● |
| `nextcloud` | Nextcloud compose stack behind host nginx, with OIDC and backup wrapper | ● |
| `immich` | Immich compose stack on a dedicated guest: derived real-IP trust list, OIDC, NFS-TLS backup landing | ● |
| `immich_ml` | Immich machine-learning (OpenVINO) compose stack in a GPU guest | ● |
| `home_assistant` | Home Assistant OS configuration deployment over the HAOS SSH add-on, checksum-idempotent | ● |

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

### Inventory-wide names the roles alias

Where a value is conventionally inventory-wide and read by several roles, the
prefixed variable is **aliased** to the conventional name, so a site can set
either one. Every alias carries a `default()`, so setting only the prefixed name
never trips an undefined-variable error on the alias:

```yaml
qol_admin_user: "{{ admin_user | default('root') }}"
base_admin_user: "{{ admin_user | default('root') }}"
nas_storage_zfs_arc_max_bytes: "{{ zfs_arc_max_bytes | default('') }}"
```

| Inventory-wide name | Aliased by |
| --- | --- |
| `admin_user` | base, qol, proxmox_vm (cloud-init user), proxmox_lxc |
| `admin_email` | base, smtp_relay |
| `ssh_port`, `ssh_permit_root_login`, `ssh_password_authentication`, `ssh_pubkey_authentication`, `ssh_authorized_keys` | base |
| `timezone` | base, immich, immich_ml |
| `dns_servers` | base, proxmox_vm (cloud-init DNS), proxmox_lxc (nameserver) |
| `internal_domain` | k3s (TLS SANs), resolv_conf, smtp_relay, proxmox_lxc (search domain), zfs_encryption (Connect URL), nextcloud |
| `external_domain` | nextcloud |
| `zfs_arc_max_bytes` | nas_storage, zfs_arc_cap |
| `host_dns_servers` | resolv_conf (set by base / adguard_home, not by the site) |
| `vm_additional_disks` | proxmox_vm (creates + attaches), k3s (passes through to zvol_mount), gitlab, immich, nextcloud |
| `redis_version` | nextcloud |
| `immich_version` | immich_ml (so one pin drives both halves) |
| `kube_vip_version`, `kube_vip_interface` | k3s |
| `nvidia_driver_version`, `nvidia_container_toolkit_version`, `nvidia_cuda_keyring_version`, `nvidia_cuda_keyring_sha256` | k3s (GPU agents; required when `k3s_gpu_node`) |

`zfs_arc_max_bytes` is read by two roles on purpose. A host that runs both
(`nas_storage` and `zfs_arc_cap`) gets the same value written to the same
`modprobe.d` file by both, which is idempotent — but keep the two role gates
mutually exclusive if the value should ever differ per role.

One alias crosses roles rather than the inventory: `nextcloud_backup_metrics_dir`
defaults to `node_exporter_host_textfile_dir`, so a guest running both roles
publishes its backup metrics where the exporter reads them without restating the
path.

## Consumers that differ from weisssrv

A consumer whose backends are not weisssrv's — Ceph instead of ZFS, a secrets
store that is not 1Password, a forge that is not GitLab — does not fork the
collection. Roles that *are* a backend (`zfs_*`, `nas_storage`, the `proxmox_*`
family) are skipped and replaced by a sibling family in the same flat FQCN
namespace; roles that merely *use* one expose a seam variable whose default is
today's behaviour (`zfs_encryption_key_command`, `proxmox_storage_defaults`,
`restic_offsite_bind_mode`, `node_exporter_host_zpool_collector`,
`acme_certs_dns_hook`, `zvol_mount_device_id_prefix`). The full seam map, the
by-design list, and the contract for contributing an alternative are in
[docs/EXTENSIBILITY.md](../../../docs/EXTENSIBILITY.md).

## Migrating from un-prefixed in-tree roles

**[MIGRATING.md](MIGRATING.md) is the master old → new map** and the list a
migration executes: every renamed variable, every externalized default (same
name, site-specific value now empty), and every required input, per role.

The rename is a breaking change and a **silent** one: each alias and each
default is `| default(...)`, so a name you miss does not raise
`AnsibleUndefinedVariable` — it quietly takes the role default. Read MIGRATING's
"How to check a migration" section before starting; the short version is that a
`--check` run catches the loud half (required-input asserts) and only a rendered-
config diff catches the quiet half.

Three rules the migration depends on:

- **Land the inventory rename and the collection adoption in the same MR.** Most
  renames have no back-compat shim. A half-migrated inventory does not fail
  cleanly — it provisions a guest with a role default (wrong ISO store, wrong
  gateway assert, an empty firewall group set).
- **Metric names are not variable names.** Several roles deliberately keep a
  metric prefix that no longer matches their variable prefix
  (`adguardhome_sync_*`, `cert_distribution_targets.prom`) because live alerts,
  promtool tests and dashboards consume those exact strings. Renaming one is an
  observability change, not a tidy-up.
- **Some roles de-provision on `enabled: false`.** `nas_storage`'s archive
  replication and `zfs_encryption`'s mount-anchor unit are removed when their
  inputs go empty, rather than being left inert. That is a live-host action the
  first time the collection reconciles a host that has them.

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

There is no `changelogs/` directory. Release notes are generated per tag by
`ci/release/semantic-release.yml` from the conventional commits in the release —
see [docs/VERSIONING.md](../../../docs/VERSIONING.md#no-changelog-file).

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
request actually touches. A role directory with a scenario and no matrix entry
fails the pipeline by design, so adding a role means adding its matrix row.

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
shipped plugin is public API from its first release. `galaxy.yml`'s `version:`
mirrors the tag and is bumped in the release MR, not after it.
