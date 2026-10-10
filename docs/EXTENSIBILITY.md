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

A gate or script a consumer forked because this library does not offer it is a
missing seam of the same kind. The queue of those, each with the site data that
has to become a flag first, is [SCRIPTS.md § Extraction
queue](SCRIPTS.md#extraction-queue) — including the ones already offered under
another name, where the work is a consumer deleting its fork rather than a
library change.

## Seam map

| Axis | Today | Seam | Where |
| --- | --- | --- | --- |
| Secrets, host-side | values resolved by the caller (`op run --`, any `op://` reference) before Ansible starts | none needed — roles take **values**, never references | all roles |
| Secrets, at boot | `zfs_encryption` fetches pool passphrases from 1Password Connect | `zfs_encryption_key_command` (empty = Connect), plus `zfs_encryption_token_path` | [`zfs_encryption`](../ansible_collections/weisssrv/infra/roles/zfs_encryption/README.md) |
| Secrets, CI-side | `ci/deploy/*` resolve credentials with the 1Password CLI | `secrets_source` (`1password` \| `env`) on `deploy-base` and `kubectl-setup`, `secret_runner` on `ansible-deploy`; `ci/review/pr-agent.yml` branches on its own `secrets_source`. `env` mode reads `$SSH_PRIVATE_KEY` / `$KUBECONFIG_B64` from masked CI variables and `secret_runner: ""` drops the `op run --` wrapper. A third backend supplies those two variables from its own `before_script`, or replaces the include as the cluster template's `secret_read()` / `secret_runner()` macros do. In `env` mode the `LOKI_PUSH_*` `op://` values must be overridden per job. | `ci/deploy/`, `ci/review/` |
| Secrets, in-cluster | External Secrets Operator with the 1Password Connect provider | not in this library — manifests live in the cluster template | — |
| Storage, guest placement | Proxmox storage picked from the host's `proxmox_role` | `proxmox_storage_defaults` (role → storage id), `proxmox_storage` / `proxmox_lxc_storage` per guest | `proxmox_vm`, `proxmox_lxc` |
| Storage, guest disks | zvols attached at a QEMU SCSI by-id path | `zvol_mount_device_id_prefix` — the role itself is UUID/fstab based, not ZFS-aware | `zvol_mount` |
| Storage, backup source | restic walks a ZFS snapshot, discovered and cloned with `zfs` | none — `restic_offsite_bind_mode` (`bind` binds the `.zfs/snapshot` subtree, `direct` reads the mountpoint as-is) is a test-harness switch, not a storage-backend seam; see the role README under ## Tunables (Paths and layout). A non-ZFS consumer needs a sibling role | `restic_offsite` |
| Storage, offsite target | rclone remote of type `b2` (Backblaze) | `restic_offsite_rclone_remote_type` + `restic_offsite_rclone_remote_options` (rendered verbatim as `key = value`); only `b2` has named credential variables | `restic_offsite` |
| Storage, metrics | `zpool status` collector ships with the Proxmox collectors | `node_exporter_host_zpool_collector` (defaults to `node_exporter_host_proxmox`) | `node_exporter_host` |
| Certificates | acme.sh DNS-01 via Cloudflare | `acme_certs_dns_hook` — any dnsapi hook the pinned tarball ships; `acme_certs_ca_server` — any CA acme.sh accepts (letsencrypt, zerossl, google, buypass, or a CA directory URL) | `acme_certs` |
| Forge | GitLab CI templates, GitLab release/MR APIs | `--platform {gitlab,github}` on `semantic-release.py`; the three vendorable Actions workflows (`ci/github/`, `ci/release/*.example.yml`) | `ci/`, `scripts/` |
| DNS / tailnet / IdP / LAN, as code | one Terraform module per provider: Cloudflare, Tailscale, Authentik, UniFi | none — an alternative provider is a **sibling module**, not a flag (below) | `terraform/modules/` |
| Template source | `new-cluster` / `new-app` default to the published GitLab template URLs | the `source` positional takes any copier template — a VCS URL on any host, or a local path — and `--vcs-ref` picks the tag | `cli/weisssrv_lib_cli/templates.py` |
| Host firewall implementation | iptables `pve-firewall` | `proxmox_firewall_nftables` renders `nftables: 1` into the node's `host.fw`, selecting the nftables `proxmox-firewall`; omitted when false | [`proxmox_firewall`](../ansible_collections/weisssrv/infra/roles/proxmox_firewall/README.md) |

### Known gaps in the CI secrets seam

`secrets_source: env` covers the SSH key and the kubeconfig, and two couplings
stay the consumer's to handle:

- `LOKI_PUSH_USER` / `LOKI_PUSH_PASSWORD` are `op://` strings only `op run`
  resolves, so `env` mode overrides them on the job.
- `deploy-base` still extends `.install-1password`, so the CLI is installed even
  when nothing calls it. Removing that would mean a `*_ref` / `base_fragments`
  input on every deploy template, a larger breaking change than the seam is
  worth today.

`ci/deploy/ansible-deploy.yml` is shipped but adopted by no consumer: a real
deploy job carries site-specific `--limit` and `--tags` the template does not
model. `ci/templates/docker-dind.yml` is likewise shipped without
`ci/build/docker-build.yml` extending it — that would force the new file into
every consumer's include block and break the app template's rendered tenant
pipeline until it is re-tagged. `tests/test_pin_parity.py` holds their pins
equal instead.

## Backend-specific by design

These roles *are* the backend. They are not seam candidates — an alternative is
a sibling role, not a flag:

- **ZFS**: `nas_storage`, `zfs_encryption`, `zfs_arc_cap`, `zfs_exporter`,
  `restic_offsite` (`nas_storage_skip_zfs_operations` is a CI-image escape
  hatch, not a storage-backend switch). `nas_storage` also carries
  backend-neutral services — Samba shares, smartd, swap-clean and the
  media-mover timer — behind their own variable gates. A non-ZFS consumer
  re-implements those alongside its `ceph_*` roles, or contributes the
  extraction.
- **Proxmox**: `proxmox_vm`, `proxmox_lxc`, `proxmox_firewall`, `proxmox_ha`,
  `proxmox_backup`, `vfio_passthrough`.
- **One service each**: `gitlab`, `plex`, `nextcloud`, `immich`, `immich_ml`,
  `home_assistant`, `adguard_home`, `unbound`, `k3s`, `tailscale`.

Everything else is backend-neutral already: `base`, `qol`, `apt_signed_repo`,
`resolv_conf`, `nic_tuning`, `encrypted_swap`, `nfs_tls`, `smtp_relay`,
`postfix_null_client`, `adguard_sync`, `prometheus_exporter`,
`textfile_collector`, `node_exporter_host`, `unbound_exporter`, `alloy_host`,
`zvol_mount`, `acme_certs`, and the compose scaffolding `docker_engine` and
`compose_app`, which the service roles share.

**`terraform/modules/` follows the same rule.** All four shapes are
provider-locked (`cloudflare-zone`, `tailscale-acl`, `authentik-sso`,
`unifi-network`), so a Route53 consumer adds a `route53-zone` module *beside*
`cloudflare-zone` rather than adding a provider switch to it — same naming
convention (`<provider>-<object>`), same README-states-the-provider rule. A
consumer with no tailnet, an IdP that is not Authentik, or a gateway that is
not UniFi, simply calls fewer modules: each root is a thin caller holding only
site data, and no module includes another.
The one seam inside a module is the usual defaults rule — a new variable
defaults to today's rendered plan, proven by a zero-diff `terraform plan`. The
floor and provider-pinning policy every module follows is
[../terraform/modules/README.md](../terraform/modules/README.md).

## Side-by-side role families

The collection is a flat `roles/` namespace addressed by FQCN, and a playbook
names the roles it wants. A Ceph consumer therefore does not fork or fence
anything: it omits `weisssrv.infra.zfs_*` and `nas_storage` from its plays and
adds its own `ceph_*` roles — from its own collection, or contributed here as a
second family alongside `zfs_*`. Both families can be installed at once; only
the playbook decides which runs.

What makes that work, and must keep working:

- No role includes another role from a different backend family. Every
  cross-role include today targets either a backend-neutral role (the list
  above) or a role in the same family — `nas_storage` → `zfs_arc_cap`,
  `proxmox_lxc` → `proxmox_vm` and `compose_app` → `docker_engine` are the
  three same-family cases. Adding an include whose target is neither is the one
  change that breaks a side-by-side family.
- Inventory-wide aliases (`internal_domain`, `zfs_arc_max_bytes`, …) are read
  with `| default(...)`, so a host that runs none of a family never trips on an
  undefined variable.

## Forge portability

The `ci/` templates are GitLab CI YAML and stay that way — a GitHub consumer
does not `include:` them. What has to be portable is what they *call*:

- **`scripts/` are forge-agnostic by default**; [SCRIPTS.md](SCRIPTS.md) names
  the exceptions in its Forge column. They read `CI_*` variables where present
  but do the work locally, so a GitHub consumer vendors the script and calls it
  from a workflow step.
- **`semantic-release.py` takes `--platform {gitlab,github}`** (default
  `gitlab`). The bump decision and the notes are forge-neutral; only the
  tag/release API call differs.
  [`ci/release/github-release-workflow.example.yml`](../ci/release/github-release-workflow.example.yml)
  is the reference Actions workflow, vendored rather than included.
- **The gate set has an Actions counterpart too**:
  [`ci/github/ci.example.yml`](../ci/github/ci.example.yml) (yamllint,
  kustomize + kubeconform, shellcheck, doc links, secret scan) and
  [`ci/github/build-image.example.yml`](../ci/github/build-image.example.yml).
  Same discipline: vendored byte-identically and re-vendored on a bump. The copy
  relationship lives in each consumer's `scripts/vendored-manifest.yml` (these
  files are on the offer list, [`scripts/vendorable-paths.yml`](../scripts/vendorable-paths.yml))
  and is checked by [`scripts/check-vendored-copies.py`](../scripts/check-vendored-copies.py),
  which is what reaches past `scripts/` — a `scripts/`-only iterator cannot see
  `.github/`.
- **Known gap — the GitHub Actions cluster pipeline.** A tenant repo already
  has a complete `github` CI shape (the app template renders it); a CLUSTER
  repo does not — its deploy/validate/verify pipeline exists only as GitLab CI,
  so the cluster template's `git_backend: github` is validator-blocked until an
  Actions equivalent exists. Nothing new should be built in a GitLab-only shape
  in the meantime.
- **The gitlab-only scripts are the porting bill.** The `gitlab-only` rows in
  [SCRIPTS.md](SCRIPTS.md#forge-coupling) are the list. Only
  `version-bump-mr.py` needs porting — the same `--platform` flag and a PR
  call, not a second script. The rest parse or emit GitLab CI YAML,
  `ci_yaml.py` being the shared loader they import, so an Actions pipeline
  replaces them together rather than porting them. Keeping one instead means a
  pluggable extractor, not a second script: `check-lib-pins.py` needs one
  returning `[(pin_location, ref)]` from `uses: org/repo/...@ref`, and
  `check-deploy-coverage.sh` / `check-deploy-preflight.py` need one returning
  each deploy job's trigger paths from `paths:` / `paths-ignore:`.
- Nothing in the Ansible collection assumes a forge. `gitlab` is a role that
  *installs* GitLab; the only other forge-shaped input is `gitlab_api_token`.

## Answer names across the family

The cluster template and the app template ask some of the same questions under
different names. An operator copying cluster answers into a tenant needs the
mapping.

| Cluster template | App template | Same thing? |
|---|---|---|
| `metallb_internal_vip` | `internal_vip` | Yes — the same LAN-only ingress VIP. The rename is deferred to the next app-template MAJOR, because every existing tenant must edit `.copier-answers.yml` before its next `copier update`. |
| `git_backend` (`gitlab_selfhosted`, `github`) | `ci_shape` (`gitlab_selfhosted`, `github`, `none`) | No — deliberately distinct. `git_backend` is the forge the cluster repo lives on; `ci_shape` is the pipeline the tenant repo renders, and `none` renders no pipeline at all. |
| `secrets_backend` (`onepassword`) | `secrets_backend` (`onepassword`, `gitlab`, `none`) | Same name, different scope. The cluster answer covers host tooling and in-cluster secrets; the tenant answer covers in-cluster only. The cluster template's tenants README shows a 1Password ClusterSecretStore only, so the operator side of the tenant `gitlab` backend has no worked example yet. |

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
4. **Versioning.** A new role or a new variable is a minor bump; a changed
   default or a rename is major. See [VERSIONING.md](VERSIONING.md).
5. **Register it.** Add the row to the seam map above. A new consumer registers
   nothing here — it records its own pins and, if it vendors files, its own
   `scripts/vendored-manifest.yml`.
