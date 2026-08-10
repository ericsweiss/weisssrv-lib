# Extensibility

How a consumer that is **not** weisssrv adopts this library: a different secrets
backend, storage that is not ZFS, a forge that is not GitLab.

Nothing here is an alternative implementation — the library ships one of each
today. What it ships is the **seams**: named variables whose default is the
current behaviour, and a layout that lets an alternative live beside the
existing one instead of forking it.

Two rules govern every seam:

- **The default is today's behaviour.** Setting no new variable produces a
  byte-identical result. A seam is never a migration.
- **Site data is an input, not a seam.** Domains, IPs, pool names and
  credentials are already inputs (collection
  [README](../ansible_collections/weisssrv/infra/README.md)). A seam is only
  needed where a role hardcodes a *mechanism*.

## Seam map

| Axis | Today | Seam | Where |
| --- | --- | --- | --- |
| Secrets, host-side | values resolved by the caller (`op run --`, any `op://` reference) before Ansible starts | none needed — roles take **values**, never references | all roles |
| Secrets, at boot | `zfs_encryption` fetches pool passphrases from 1Password Connect | `zfs_encryption_key_command` (empty = Connect), plus `zfs_encryption_token_path` | [`zfs_encryption`](../ansible_collections/weisssrv/infra/roles/zfs_encryption/README.md) |
| Secrets, in-cluster | External Secrets Operator with the 1Password Connect provider | not in this library — manifests live in the cluster template | — |
| Storage, guest placement | Proxmox storage picked from the host's `proxmox_role` | `proxmox_storage_defaults` (role → storage id), `proxmox_storage` / `proxmox_lxc_storage` per guest | `proxmox_vm`, `proxmox_lxc` |
| Storage, guest disks | zvols attached at a QEMU SCSI by-id path | `zvol_mount_device_id_prefix` — the role itself is UUID/fstab based, not ZFS-aware | `zvol_mount` |
| Storage, backup source | restic walks a ZFS snapshot bind-mount | `restic_offsite_bind_mode: direct` walks a plain path instead | `restic_offsite` |
| Storage, metrics | `zpool status` collector ships with the Proxmox collectors | `node_exporter_host_zpool_collector` (defaults to `node_exporter_host_proxmox`) | `node_exporter_host` |
| Certificates | acme.sh DNS-01 via Cloudflare | `acme_certs_dns_hook` — any dnsapi hook the pinned tarball ships | `acme_certs` |
| Forge | GitLab CI templates, GitLab release/MR APIs | `--platform {gitlab,github}` on `semantic-release.py`; the Actions example workflow | `ci/`, `scripts/` |

## Backend-specific by design

These roles *are* the backend. They are not seam candidates — an alternative is
a sibling role, not a flag:

- **ZFS**: `nas_storage`, `zfs_encryption`, `zfs_arc_cap`, `zfs_exporter`
  (`nas_storage_skip_zfs_operations` is a CI-image escape hatch, not a
  storage-backend switch).
- **Proxmox**: `proxmox_vm`, `proxmox_lxc`, `proxmox_firewall`, `proxmox_ha`,
  `proxmox_backup`, `vfio_passthrough`.
- **One service each**: `gitlab`, `plex`, `nextcloud`, `immich`, `immich_ml`,
  `home_assistant`, `adguard_home`, `unbound`, `k3s`, `tailscale`, and the
  compose scaffolding (`docker_engine`, `compose_app`).

Everything else is backend-neutral already: `base`, `qol`, `apt_signed_repo`,
`resolv_conf`, `nic_tuning`, `encrypted_swap`, `nfs_tls`, `smtp_relay`,
`postfix_null_client`, `adguard_sync`, `prometheus_exporter`,
`textfile_collector`, `node_exporter_host`, `unbound_exporter`, `alloy_host`,
`restic_offsite`, `zvol_mount`, `acme_certs`.

## Side-by-side role families

The collection is a flat `roles/` namespace addressed by FQCN, and a playbook
names the roles it wants. A Ceph consumer therefore does not fork or fence
anything: it omits `weisssrv.infra.zfs_*` and `nas_storage` from its plays and
adds its own `ceph_*` roles — from its own collection, or contributed here as a
second family alongside `zfs_*`. Both families can be installed at once; only
the playbook decides which runs.

What makes that work, and must keep working:

- No role includes another role from a different backend family. The only
  cross-role includes today are to shared scaffolding
  (`prometheus_exporter`, `textfile_collector`, `resolv_conf`, `zvol_mount`,
  `apt_signed_repo`, `compose_app`, `docker_engine`) — all backend-neutral.
- Inventory-wide aliases (`internal_domain`, `zfs_arc_max_bytes`, …) are read
  with `| default(...)`, so a host that runs none of a family never trips on an
  undefined variable.

## Forge portability

The `ci/` templates are GitLab CI YAML and stay that way — a GitHub consumer
does not `include:` them. What has to be portable is what they *call*:

- **`scripts/` are forge-agnostic by default.** They read `CI_*` variables where
  present but do the work locally; a GitHub consumer vendors the script and
  calls it from a workflow step. `docs/SCRIPTS.md` is the contract.
- **`semantic-release.py` takes `--platform {gitlab,github}`** (default
  `gitlab`). The bump decision and the notes are forge-neutral; only the
  tag/release API call differs.
  [`ci/release/github-release-workflow.example.yml`](../ci/release/github-release-workflow.example.yml)
  is the reference Actions workflow, vendored rather than included.
- **The known gap** is `scripts/version-bump-mr.py`, which speaks the GitLab MR
  API only. Adding GitHub support means the same `--platform` flag and a PR call
  — not a second script.
- Nothing in the Ansible collection assumes a forge. `gitlab` is a role that
  *installs* GitLab; the only other forge-shaped input is `gitlab_api_token`.

## Contract for adding an alternative

1. **Naming.** A new backend family gets its own role prefix (`ceph_osd`, not
   `nas_storage_ceph`), and every variable carries the role prefix — that is
   enforced by `ansible-lint`'s `var-naming[no-role-prefix]`.
2. **Defaults.** A seam variable added to an existing role defaults to the
   current behaviour, and the role's README says so in the same commit. A seam
   that changes any rendered file with no variable set is a breaking change, not
   a seam. Prove it: render the template both ways and diff.
3. **Molecule.** Every role has a scenario, and CI fails a role directory with
   a scenario and no matrix row
   (`ci/internal/molecule-matrix.gitlab-ci.yml`). A seam added to an existing
   role needs coverage of the *non-default* branch only if the branch renders
   different content.
4. **MIGRATING entry.** Only if a consumer must act. New variables with
   behaviour-preserving defaults do not belong in
   [MIGRATING.md](../ansible_collections/weisssrv/infra/MIGRATING.md) — a
   rename, a removal or a changed default does.
5. **Versioning.** A new role or a new variable is a minor bump; a changed
   default or a rename is major. See [VERSIONING.md](VERSIONING.md).
6. **Register it.** Add the row to the seam map above, and the consumer to
   [CONSUMERS.yml](CONSUMERS.yml) if it is a new one.
