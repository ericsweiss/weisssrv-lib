# Migrating to weisssrv.infra

Every role variable in this collection carries its role's name as a prefix. That
is consumer-visible API, so the rename from an un-prefixed in-tree role is a
breaking change — and a **silent** one: each alias and each default is
`| default(...)`, so a name you miss does not raise `AnsibleUndefinedVariable`,
it quietly takes the role default. `adguard_tls_server_name` left behind in
`group_vars` renders an empty DoT SNI on both resolvers, on every deploy, with a
green play.

This file is the complete old -> new map, role by role: every renamed variable,
every externalized default (same name, site value now empty) and every required
input. It is mechanical on purpose: work through it once per adopted role rather
than trusting a grep.

Six roles — `gitlab`, `home_assistant`, `immich`, `immich_ml`, `nextcloud`,
`plex` — are **new to the collection**. For those, "migrating" means deleting the
in-tree role, pointing the playbook at `weisssrv.infra.<role>`, and supplying the
site values that used to be role defaults. Their sections carry both.

**Land the inventory changes and the collection adoption in the SAME merge
request.** Most renames have no back-compat shim, and several roles now assert
inputs that used to be defaults — a half-migrated inventory does not fail
cleanly, it provisions with a role default.

---

# Unreleased (next release)

Nothing yet.

# v0.11.1

No migration steps. Additive only: `nextcloud` gains
`nextcloud_smtp_user`/`nextcloud_smtp_password` — both set enables
`mail_smtpauth` for an authenticated submission relay (587 + STARTTLS),
both empty keeps the legacy network-trusted posture, and removal converges
auth back off.

# v0.11.0

No migration steps. Additive only:

- `restic_offsite` gains `restic_offsite_keep_tags` (default `[]`): entries
  become `--keep-tag` flags on the shared retention array, so tagged
  snapshots are never forgotten — the pin for immutable data whose paths the
  nightly run excludes. `restic-offsitectl` gains a bare `restic`
  passthrough subcommand for one-off authenticated ops (e.g. tagging).
- Every consumer-included CI job template now declares right-sized
  `KUBERNETES_MEMORY_LIMIT`/`_REQUEST` via new `job_memory_limit`/
  `job_memory_request` inputs (defaults per job class), so concurrent
  pipelines pack into the runner namespace quota instead of each job
  costing the runner-wide default. Override per consumer only where a job
  genuinely needs more.
- The `authentik-sso` Terraform module gains an optional `users` map
  (identity-only user accounts, `prevent_destroy`); group membership
  resolution prefers managed users and falls back to pre-existing ones.

# v0.10.0

No migration steps. Additive only:

- `nas_storage` gains `nas_storage_nfs_disable_delegations` (default `false`,
  no behaviour change unless set). Set `true` to stop nfsd granting NFSv4
  delegations via a persisted `fs.leases-enable=0` drop-in — the mitigation
  for the kernel `file_lock` slab leak in the GETATTR delegation-conflict
  path (2026-08-18 incident; role README documents the removal criteria).
- The scrape gate's label regexes now use `fullmatch()`, closing the
  trailing-newline acceptance (`"app\n"`) the `$` anchor allowed.

# v0.9.8

No migration steps. The scrape gate's selector validator now applies the
apiserver's own label rules — key/value syntax (qualified names, 63-char
bounded values) and operator cardinality (In/NotIn require non-empty values,
Exists/DoesNotExist forbid them) — completing the structural validation of
v0.9.7.

# v0.9.7

No migration steps. The scrape gate's family-credit atomicity is closed
STRUCTURALLY: a selector peer is only skipped when its whole LabelSelector is
API-valid (known keys, typed terms, string label values, well-formed
matchExpressions with known operators) — malformed selectors poison the
credit like every other invalid shape, ending the level-by-level chase.

# v0.9.6

No migration steps. The scrape gate's dual-family ipBlock credit is now fully
rule-atomic: an ipBlock of invalid SHAPE anywhere in the rule (wrong type,
unknown keys, unparseable cidr, non-list except, or combined with a selector)
disqualifies the whole rule from the credit — the API rejects the whole
policy — while a valid selector peer or a valid narrowing block merely skips.

# v0.9.5

No migration steps. Scrape-gate crediting is now atomic per rule and exact
per expression: one invalid peer disqualifies the whole rule (the API
rejects the whole policy), and a matchExpressions requirement only credits
with known fields, operator `In`, and a real list of values (a string would
do substring membership).

# v0.9.4

No migration steps. The netpol gates' shape rule now covers every level of
the object: unknown PEER keys (`podSelecter:`) and unknown RULE keys
(`form:`) never credit a scrape, and an absent `spec.podSelector` — a
REQUIRED field, not an all-pods default — neither fences, defeats, nor
registers a restriction in either gate.

# v0.9.3

No migration steps. The netpol gates extend v0.9.2's shape rule to UNKNOWN
keys: a selector carrying anything besides `matchLabels`/`matchExpressions`
(the `matchLables:` typo class), or an `ipBlock` carrying anything besides
`cidr`/`except`, never fences, defeats, or credits — server-side apply
rejects those objects, so they must not act on any verdict.

# v0.9.2

No migration steps. Both netpol gates now validate SHAPES before crediting or
counting: wrong-typed selector terms (`matchLabels: []`, `matchExpressions:
{}`), a peer combining `ipBlock` with a selector, and a falsey non-list
`except` are API-invalid and neither fence a namespace, defeat a fence, nor
prove a scrape is admitted; non-dict rules/expressions no longer traceback.

# v0.9.1

No migration steps. `check-default-deny-coverage.py`'s except-subtraction now
counts only entries the API would accept (a strict subnet of the cidr): a `/0`
allow "excepted by itself" no longer certifies a fence the rejected policy
does not provide.

# v0.9.0

## Breaking — act in the same MR as the bump

| Surface | What changed | What to do |
|---|---|---|
| Vendored-copy registry | INVERTED. The library no longer knows its consumers: `scripts/vendored-paths.yml` (per-consumer registry) and `docs/CONSUMERS.yml` (adoption ledger) are gone. The library now ships `scripts/vendorable-paths.yml` — an OFFER list of the paths it supports vendoring — and `scripts/check-vendored-copies.py` reads a CONSUMER-OWNED manifest instead (`--consumer NAME`/`--registry` dropped; `--manifest FILE` added, defaulting to `<repo-root>/scripts/vendored-manifest.yml`). | Create `scripts/vendored-manifest.yml` in the consumer, holding what the old registry's block for that consumer held (same `vendored:`/`forked:` entry forms, `reason:` + `reconciled_sha256` on forks), and drop `--consumer`/`--registry` from every gate invocation. A manifest `lib:` path must appear in the offer list at the pinned ref — vendoring an unoffered file now fails. Upside of owning the manifest: moving a vendored file inside a consumer repo is no longer a library-release event, and a fork's `reconciled_sha256` is re-taken where the fork lives. |
| `check-default-deny-coverage.py` / `check-scrape-netpol.py` selector and ipBlock semantics | Both gates now read selectors as the API does, and both can turn a green pipeline red at adoption. Default-deny gate: `podSelector: {matchLabels: {}}` / `{matchExpressions: []}` is namespace-wide (a fence spelled that way stops failing; a wide-open allow spelled that way starts failing), and a `/0` `ipBlock` peer is wide open unless its `except` list reconstructs the entire address family (exact subtraction — no assumption about which ranges a cluster's pods occupy). Scrape gate: those same namespace-wide spellings now REGISTER as restricting a namespace — a namespace whose only default-deny used an empty-termed selector was previously invisible to the scrape gate and must now prove it admits observability; a rule whose unexcepted `/0` `ipBlock` peers span BOTH address families is credited as admitting it (one family alone proves nothing about the scraper's family and stays a finding). | Re-run `flux:lint` at adoption. Where the scrape gate newly fails, add the observability allow the namespace always needed (the gate was blind, the scrape was already broken); where the default-deny gate newly flags a `/0`-with-partial-excepts allow, narrow the CIDR to what the rule actually means to admit — a `/0` ingress allow is not a fence-compatible peer. |
| `proxmox_lxc` idmap asserts | The range-membership assert (`proxmox_lxc_idmap_uid`/`_gid` `<` `proxmox_lxc_idmap_range`) now runs for EVERY unprivileged container; it was gated behind `proxmox_lxc_gpu_passthrough`, so a non-GPU container with an out-of-range point was created and then refused by `pct start`. | Nothing, unless an existing non-GPU container carries an out-of-range idmap point — the play now fails at the assert instead of at `pct start`; fix the inventory value it names. |

## Behaviour changes — no action, but read before adopting

| Surface | What changed |
|---|---|
| `nas_storage` | New pre-mount task detaches an export bind whose live source filesystem was deleted under it (`findmnt` source suffixed `//deleted` after a dataset migration/rename) so the fstab remount serves the new tree — previously the role read the stale bind as converged and every client mount RPC hung. |
| `check-versions.py` JSON | A held service now reports `update_available: false` per-service (visibility stays via `held: true` + `latest_version`), so JSON consumers cannot act on a held update without deliberately parsing the hold. |
| `ci/maintenance/version-check.yml` | New optional `github_token` input (default `"$GITHUB_TOKEN"`) forwarded to the job's `GITHUB_TOKEN` variable — pass a variable reference like `"$GH_API_TOKEN"`, never a literal. |

# v0.8.0

## Breaking — act in the same MR as the bump

| Surface | What changed | What to do |
|---|---|---|
| `adguard_home` dependencies | `meta/main.yml` no longer declares `weisssrv.infra.unbound`. The role used to install, configure and start unbound on every AdGuard host regardless of `adguard_home_upstream_dns` — which is what made "point it at a public resolver to drop that dependency" untrue. | Apply `weisssrv.infra.unbound` **before** `weisssrv.infra.adguard_home` in the playbook if you are on the default `127.0.0.1:5335` upstream; the post-deploy dig probe resolves through it. Nothing to do at a public upstream, beyond emptying `adguard_home_after_units` / `_wants_units` as before. A host that already runs unbound keeps running it — the role never removed it, and now simply stops re-converging it. |
| `unbound` legacy drop-ins | `unbound_legacy_dropins` now defaults to `[]`; it used to name one site's file (`weisssrv.conf`), which is site data. | Name the superseded drop-in in the resolver group's inventory if the hosts still carry one — unbound merges `/etc/unbound/unbound.conf.d/` with a SORTED glob, so a leftover that sorts after the managed file wins every scalar it duplicates. |
| `qol` dependencies | `meta/main.yml` no longer declares `weisssrv.infra.base`, so running dotfiles no longer applies SSH hardening, fail2ban and resolv.conf management as a side effect. | List `base` ahead of `qol` in the play where the admin account and its home must exist first. `qol_admin_user` still aliases `admin_user`, which is what keeps the two roles on the same account. |
| `nas_storage` Samba guest mapping | `map to guest = never` (was `bad user`): an unknown user gets an auth failure instead of being mapped to the guest account. | Nothing to do unless a share sets `guest_ok: true` — those shares stop serving unauthenticated clients. Give those clients real accounts, or pin `map to guest = bad user` in the share's own config. The first converge restarts smbd. |
| Terraform module `required_version` | Floors rise so the shipped `terraform test` suites can run: `authentik-sso` needs `>= 1.11` (`override_during`), `cloudflare-zone` and `tailscale-acl` need `>= 1.7` (`mock_provider`). | Nothing to do at Terraform 1.11+ (all known consumers run 1.15). A root below its module's floor fails `init` until the binary is upgraded. |
| CLI exit codes | `weisssrv-lib-cli` exits **3** (was 2) when copier is not installed; 2 now exclusively means a validation failure. | Update any wrapper that branches on the exit code; the README's exit table is the contract. |
| `scripts/check-hpa-vpa-invariant.py` (vendored) | Under `--require-chart-native-vpas` it also enforces the VPA memory-cap rule: `maxAllowed.memory` **above** a container's memory limit fails in every shape, and **equal to** it fails where the policy also controls limits (`controlledValues: RequestsAndLimits` or unset, mode not `Off`). `RequestsOnly` cap == limit stays correct, and a target the kustomize corpus does not render is skipped. | The re-vendor turns this on, so run the gate over the rendered corpus in the bump MR: re-derive each flagged cap from its limit (same commit as any limit change), or park it in the policy file's new `vpa_cap_allowlist` (`namespace/VerticalPodAutoscaler/name`, one rationale per entry) while it waits. |

## Newly asserted — loud where it used to be silent

| Role | What is asserted now | Why it used to be silent |
|---|---|---|
| `acme_certs` | every `acme_certs_distribution_targets` entry declares a NON-EMPTY `restart_service` **or** `restart_command` | neither key rendered `RELOAD='systemctl restart '` into the receiver, which then failed only on the first run that actually pushed a cert; an empty string passed the presence check. The receiver now also refuses to record a cert as applied when no reload was baked in (exit 5) |
| `adguard_home` | a staged archive matches `adguard_home_archive_sha256` or an entry in a `checksums.txt` staged beside it | the cache path skipped `get_url`'s checksum entirely and installed whatever was on disk |
| `alloy_host` | no `alloy_host_extra_args` entry contains a `"` | the args are joined into a double-quoted `CUSTOM_ARGS=` assignment, so an embedded quote silently changed what the unit runs |
| `proxmox_lxc` | `proxmox_host` is set (its undocumented `local-lvm` storage fallback is gone with it) | every pct/pvesh call delegates to it, so the run failed later with a message about delegation |
| `proxmox_lxc` | `proxmox_lxc_searchdomain` (alias `internal_domain`) is non-empty on the create path | `pct create --searchdomain ""` succeeded |
| `proxmox_lxc` | `proxmox_lxc_idmap_gid` sorts above `proxmox_lxc_video_gid` and `proxmox_lxc_render_gid` on a GPU container | a lower value emitted overlapping `lxc.idmap` ranges and `pct start` refused the container |
| `proxmox_backup` | `id`/`type`/`content` per storage entry, `id`/`storage`/`schedule` per vzdump job | a missing key surfaced as a Jinja undefined after the `pvesh get` reads had already run |
| `unbound_exporter` | `ansible_architecture == 'x86_64'` | the role installs the upstream `.x86_64.deb` that `unbound_exporter_checksum` pins; another architecture 404'd or failed in dpkg |

## Changed defaults and behaviour

- `nas_storage` no longer carries its own ARC-cap implementation: it includes
  `weisssrv.infra.zfs_arc_cap` and passes `nas_storage_zfs_arc_max_bytes`
  through. The variable and its alias are unchanged, but the rendered
  `/etc/modprobe.d/zfs.conf` differs, so the next converge on a capped NAS
  rebuilds the initramfs once. Do not also list `zfs_arc_cap` in the NAS play.
- `nextcloud_oidc_allow_local_remote_servers` is RENDERED rather than used as a
  task gate, and the reconcile no longer sits behind `nextcloud_oidc_enabled`.
  The guard is widened only while OIDC is on **and** the toggle is `true`, so
  turning either off now restores Nextcloud's SSRF guard on a host where an
  earlier run widened it (it previously stayed widened forever).
- `base_ssh_permit_root_login` accepts YAML's unquoted `no`/`yes` (booleans):
  the value is normalized to sshd's spelling in the new derived
  `base_ssh_permit_root_login_effective`, which the hardening drop-in, the
  `sshd -T` check and the lockout guard all read.
- Guest firewalls honour `guest_firewall_log_level_in`, defaulting to
  `proxmox_firewall_log_level_in` (previously a hard-coded `nolog`), so a guest
  can be put into triage mode the same way a host can.
- The GitLab fail2ban jail matches `_SYSTEMD_UNIT=<gitlab_ssh_service_name>.service`
  instead of a hard-coded `ssh.service`.
- The MergerFS health probe derives its required fstab options from each
  union's own `options` instead of two hard-coded ones, so a union declaring a
  different option set is no longer permanently classified "needs remount".
- `nas_storage`'s archive replication emits `archive_backup_last_prune_success`;
  a failed `zfs destroy` no longer passes silently. The gauge previously read a
  flag set in the forked per-dataset child and lost with it, so it was a
  constant `1`; it now reports the run's real retention state.

**One-off writes and restarts** — real changes to live state on the first
converge after the bump. Budget for them in the deploy plan rather than reading
them as drift:

| Role | What moves | Consequence |
|---|---|---|
| `gitlab` | gitlab.rb's banner comments become one-line section headers. | Notifies `Reconfigure gitlab` — a full `gitlab-ctl reconfigure` with the service bounce it implies. Budget a GitLab window; nothing in the rendered configuration changes. |
| `immich` | immich.env drops a restating comment. | `Restart compose stack` — one Immich outage window. |
| `nextcloud` | Both exporter publications gain an explicit bind address (NEW `nextcloud_exporter_bind_address` / `_postgres_exporter_bind_address`, both defaulting to `0.0.0.0` = today's binding). | `Restart compose stack` — one Nextcloud outage window. The binding is unchanged. |
| `nas_storage` | smb.conf drops restating comments, and `map to guest` changes (see Breaking). | One smbd restart, which drops established SMB sessions. |
| `nas_storage` | smartd.conf's trade-off narration collapses into the flag legend. | One smartd restart. Same disks, same `-s` schedules. |
| `unbound` | unbound-managed.conf drops restating section comments. | One `Restart unbound` per resolver; the handler serializes them, so keep the usual one-resolver-at-a-time window. |
| `alloy_host` | config.alloy drops two restating comments. | One `Restart alloy`; journald shipping resumes on restart. |
| `base` | jail.local drops restating comments. | One `Restart fail2ban`. Jails, bans and ignore lists are unchanged; in-memory ban state is lost as it is on any restart. |
| `postfix_null_client`, `smtp_relay` | main.cf/master.cf/aliases/virtual/sasl_passwd drop restating comments; master.cf gains a header stating that it replaces Debian's packaged table. | `Reload postfix`, `Newaliases` and the `Postmap` rebuilds fire once. A reload, not a restart — no queued mail is affected. |
| `zfs_arc_cap` | The modprobe.d header no longer claims the file is compute-host-only, because `nas_storage` now renders it too. | One `update-initramfs -u` per capped host — including the NAS, which is separately re-rendered by the ARC-cap consolidation above. |
| `proxmox_firewall` | host.fw drops two restating comments. | One `pve-firewall` reload per node; rules unchanged. |
| `vfio_passthrough` | The grub template's notify-list comment is corrected (it names the 2 handlers the task really notifies). | One `update-grub` per VFIO host and a reboot-required warning; the rendered cmdline is unchanged, so the reboot can ride the next maintenance window. |
| `adguard_sync` | adguardhome-sync.yaml's header and the `api.port` comment are rewritten. | Systemd daemon-reload only; the next timer run picks the config up. |

## New variables (defaults preserve today's behaviour)

| Role | Variable | Default | What it unlocks |
|---|---|---|---|
| `adguard_home` | `adguard_home_archive_cache_dir` | `""` | Opt-in local mirror of the release tarball, named `AdGuardHome_linux_<arch>-v<version>.tar.gz`. Empty (the default) -> the GitHub download runs exactly as before, so an existing host sees no change. Set it, and a staged archive for the current pin is installed instead. |
| `adguard_home` | `adguard_home_archive_sha256` | `""` | Digest a staged archive must match. Empty falls back to a `checksums.txt` staged in the same directory; with neither, a staged archive FAILS the play rather than being installed unverified. |
| `alloy_host` | `alloy_host_extra_args` | `[]` | Extra Alloy CLI arguments appended to the managed `CUSTOM_ARGS` line — where a `--server.http.listen-addr` matching `alloy_host_http_port` goes. |
| `k3s` | `k3s_kubelet_args` | `[]` | Declared; both config templates already read it. |
| `nas_storage` | `nas_storage_mergerfs_required_opts` | `[]` | fstab options the MergerFS health probe requires; empty derives them from each union's own `options`. |
| `nas_storage` | `nas_storage_zfs_arc_skip_initramfs` | `false` | Passed through as `zfs_arc_cap_skip_initramfs`: render `/etc/modprobe.d/zfs.conf` but skip the `update-initramfs` rebuild, for molecule and check-mode runs with no real `/boot`. |
| `nas_storage` | `nas_storage_swap_clean_*`, `_zfs_scrub_enabled` / `_zfs_scrub_schedule`, `_smartd_enabled`, `_backup_artifact_metrics_dir`, `_media_mover_min_age` / `_media_mover_schedule` | unchanged | Declared in `defaults/` instead of existing only as template fallbacks. |
| `nextcloud` | `nextcloud_exporter_bind_address` / `nextcloud_postgres_exporter_bind_address` | `0.0.0.0` | Narrow the unauthenticated exporter publications instead of relying only on the guest firewall. |
| `proxmox_firewall` | `proxmox_firewall_cluster_rules` / `proxmox_firewall_host_rules` | `[]` | Declared; both were already documented and read by the templates. |

## Scheduled removals

The legacy migration cleanups in `base` (the `atlantic-gro-fix` /
`e1000e-tso-fix` oneshots), `docker_engine` (the pre-standardization Docker repo
line) and `adguard_sync` (the root-owned sync home) run on every host on every
run for artefacts only the original site ever had. They are removed at the next
breaking release, together with the molecule assertions that check for their
absence; a consumer that adopted the collection after v0.7.0 never had them.

> **Lifecycle.** There is no changelog file for the collection
> ([VERSIONING.md](../../../docs/VERSIONING.md) § No changelog file), so this is
> the only per-release migration record. At tag time the release MR **retitles
> this heading to the tag being cut** and opens a fresh empty
> `# Unreleased (next release)` above it — a checklist bullet in VERSIONING
> covers it, and `tests/test_migrating_sections.py::TestMigratingSections` fails a
> bump whose newest titled section is not `galaxy.yml`'s version, or a released
> tag with no section at all. A release with nothing to
> migrate still gets a section saying so; "no section" and "nothing to do" must
> not look the same. Released sections are kept **in full, newest first**, so a
> consumer jumping several releases works through each delta in order instead of
> reading two releases' breaking changes as one pending set. Nothing here is ever
> squashed or replaced; prune a section only when no supported consumer can
> still be on the release below it.

---

# v0.7.4

No migration steps. The release is a `nextcloud` fix (wait out the post-upgrade
migration before running `occ`); no variable renamed, asserted or defaulted
differently.

---

# v0.7.3

No migration steps. Galaxy installs retry through forge restarts and flux-lint
catches unparseable placeholders — both CI-side, neither reaches a role's
variable API.

---

# v0.7.2

No migration steps. `prevent_destroy` on the cloudflare-zone settings override
is a Terraform-module change, outside the collection.

---

# v0.7.1

No migration steps. Gate precision only (unprovable namespace selectors,
config-deficient canonical lists).

---

# v0.7.0

Everything from [How to check a migration](#how-to-check-a-migration) down is
the one-time **v0.6.0 adoption map** — the un-prefixed -> prefixed rename a repo
works through once. This section is the delta for a consumer already on the
collection: what this release breaks, what it asserts, and what it adds.

Work it in this order. The four subsections are ordered by what fails you
first: a pipeline that will not create, a play that fails at role entry, a
default that moved under you, and only then the seams you may adopt at leisure.

## Breaking — act in the same MR as the bump

Each of these breaks a consumer that bumps without changing anything else.

| Surface | What changed | What to do |
|---|---|---|
| `proxmox_firewall` sg-metrics | Six application scrape ports are no longer built in. Only the exporters this collection's own roles bind survive (9100, 9101, 9134, 9167). | Re-declare the app ports as site data in the NEW `proxmox_firewall_metrics_scrape_ports` or those scrapes close on every node. The removed rules, as a copy-paste inventory block, are below the table. Entry schema is shared with `proxmox_firewall_dns_admin_ports`: `{port, sources[], comment?}`, `sources` a non-empty LIST (asserted; a bare scalar is rejected) — sg-metrics applies on every node, so the scrapers are named, not defaulted. |
| `proxmox_firewall` sg-dns | The resolver admin surfaces moved out of the template. The NEW `proxmox_firewall_dns_admin_ports` (`{port, sources[], comment?}`) defaults to :443 and :3000 on the admin sets ONLY — the old template also opened both to `k3s_nodes`. | Add `k3s_nodes` to the relevant entry's `sources` if an in-cluster path needs it (a reverse proxy reaching the resolver's own TLS listener, or an in-cluster scraper on the plaintext API). |
| `nas_storage` exports | An export whose `bind_source` is outside `nas_storage_zfs_mount_roots`, is not a declared `nas_storage_mergerfs_mounts` target, and carries no explicit BOOLEAN `zfs:` key now FAILS the play. `zfs:` is load-bearing in BOTH directions: `zfs: true` applies the mounted-dataset guard and the `zfs-mount` boot ordering to a source the roots do not cover, `zfs: false` declares a plain bind. | Add the pool root to `nas_storage_zfs_mount_roots`, or set `zfs: true`/`zfs: false` on the export. A non-boolean value (`zfs: ""` from a var that rendered empty, `zfs: "maybe"`) is rejected too — it is consumed through `\| bool`, which would silently classify it as non-ZFS. |
| `nas_storage` MergerFS unions | EVERY union whose branches are all outside `nas_storage_zfs_mount_roots` — including branches EQUAL to a root, which never matched the derived pattern — now FAILS the play instead of silently losing its `x-systemd.requires=zfs-mount.service` anchor. The check no longer skips unions that omit `systemd_requires`, because the anchor is derived from the branch set, not from that key. `zfs:` on the union overrides the derivation the same way it does on an export, and must likewise be a boolean. | Add the pool root to `nas_storage_zfs_mount_roots`, or set `zfs: true`/`zfs: false` on the union. Expect changed tasks on the next converge in TWO fstab shapes: a ZFS-branched union that omitted `systemd_requires` now GAINS `nofail` and the `zfs-mount.service` anchor where it previously had neither, and a union classified NOT ZFS-backed (`zfs: false`) that declares `systemd_requires` LOSES the `x-systemd.requires=`/`x-systemd.after=zfs-mount.service` pair it was previously given unconditionally, keeping only `nofail` and its `requires-mounts-for` entries. Both are rewritten mount options on a live filesystem. |
| `nas_storage_zfs_bind_source_pattern` | An EMPTY `nas_storage_zfs_mount_roots` now derives a never-matching pattern. It used to derive `^()/`, an empty alternation matching every absolute path — so a site declaring "no ZFS roots here" got the exact opposite. | Nothing, unless you relied on the inverted behaviour; declare the sources with `zfs: true` instead. |
| `weisssrv-new-project` CLI | The `rename`, `prune`, `wire` and `verify` subcommands are REMOVED, along with the `weisssrv_lib_cli.{rename,prune,wire,verify,tree,kustomization}` modules. The app template is a copier template as of this release, so scaffolding is rendering, not mutating a fork. | Render the template instead: `new-cluster` is unchanged, and a NEW `new-app` renders the app template the same way (same flags, same optional `cluster` extra). The console script and distribution names are unchanged. Internals moved with the shape — `weisssrv_lib_cli.cluster` is now `weisssrv_lib_cli.templates` and `ClusterError` is `TemplateError`, which matters only to something importing the package rather than running the console script. `weisssrv-lib-cli` now declares NO runtime dependencies (ruamel.yaml dropped); `copier` remains the optional `cluster` extra. |
| `scripts/check-netpol-except-parity.py` | The built-in `UNRESTRICTED_EGRESS_OK` allowlist is now EMPTY, and a new `--config FILE` supplies it (`canonical_except_lists`, `fence_networks`, `unrestricted_egress_ok`). Fail-closed. | Ship a config file naming your peer-less egress rules, each with a reason — a blank reason is rejected. The canonical except-lists and fence networks keep their previous values as built-in defaults. |
| `scripts/check-alertmanager-behaviour.py` | `--config FILE` is REQUIRED; the route/alert module constants and the hard-coded extractor path are gone (`--extract-script`, `--repo-root`). | Move the routing table, synthetic alerts and upstream alerts into a config file — `examples/alertmanager-behaviour.example.yaml` is the shape. Config-load failures exit 2. |
| `scripts/check-backup-artifact-apps.py` | `--host-vars FILE` and `--rules FILE` are REQUIRED; the module constants are gone. | Pass both paths. A missing file exits 2. |
| `scripts/check-scrape-netpol.py` | Two surfaces. **Programmatic**: `main()` takes argv, the `EXEMPT_NAMESPACES` dict is gone in favour of repeatable `--exempt NS=REASON` (reason mandatory), and `OBSERVABILITY_NS` became `--observability-namespace`. **Runtime exit codes**, which reach a caller that touched neither: an EMPTY corpus on stdin is now an operator error (exit 2) where it used to pass 0, the YAML-parse arm plus a malformed `--exempt` moved from exit 1 to exit 2, and a corpus that HAS documents but holds NO SCRAPE TARGET at all is now exit 2 as well — it used to pass 0, which is how the gate stayed green when the observability stage dropped out of the render loop. | Pass exemptions on the command line. Make sure the corpus actually arrives AND covers the stage defining the ServiceMonitors/PodMonitors — either failure now reds the job. Scrape targets with none ingress-restricted among them still pass; only zero targets is the error. Any wrapper branching on `rc == 1` for "finding" versus `rc > 1` for "broken" already reads these correctly; one testing `rc != 0` as "finding" does not. |
| `scripts/check-pvc-storageclass.py` | A corpus that HAS documents but declares NO CLAIM — no PersistentVolumeClaim, no `volumeClaimTemplate`, no chart persistence block that sizes a volume — is now an operator error (exit 2) where it used to pass 0. Same zero-subjects arm `check-secretstore-scope.py` already carried, closing the last of the three stdin gates that could pass vacuously. The success line now reports the claim count alongside the document count. **Programmatic**: `violations()`, `_claim_violations()` and `_values_violations()` return `(violations, subjects_seen)` tuples instead of a bare list. | Make sure the `kustomize build` paths feeding stdin cover the stages that declare storage. A caller importing the module rather than running the script unpacks the tuple; nothing in this library or its consumers' Taskfiles does. |
| `scripts/check-taskfile.sh` | It now follows `includes:` recursively. A Taskfile that includes a fragment referencing a missing `scripts/` file FAILS where it previously passed — which is the point. | Fix the reference, or pass fragments individually. New env `CHECK_TASKFILE_MAX_DEPTH` (default 10); a missing include target is a failure, matching go-task. |
| `terraform/modules/authentik-sso` | An application that no ENABLED `policy_bindings` entry names now FAILS the plan, including a read-only drift-plan job. Reaches a consumer that passes no new input. | Audit for unbound applications and add a binding, or set `allow_unbound = true` on a tile that really is open to every authenticated user. Full entry, together with the other two `authentik-sso` additions, under [Library surfaces outside the collection](#library-surfaces-outside-the-collection). |

### The six sg-metrics rules this release deletes

They were library-side, so a consumer's inventory has no copy of them — on the
bump the six openings vanish with a green play. This is the whole set, verbatim
from v0.6.2's `cluster.fw.j2`; keep the ones your site still scrapes and drop
the rest. **31100 is the one entry whose source is `core-cluster`, not
`k3s_nodes`** — it is the Loki push NodePort (host -> k8s), not a Prometheus
scrape, and is unreconstructible from the k3s_nodes-only example in the role
README.

```yaml
proxmox_firewall_metrics_scrape_ports:
  - {port: 8123, sources: [k3s_nodes], comment: home-assistant}
  - {port: 32400, sources: [k3s_nodes], comment: plex}
  - {port: 3000, sources: [k3s_nodes], comment: adguard API}
  - {port: 7472, sources: [k3s_nodes], comment: metallb speaker}
  - {port: 7473, sources: [k3s_nodes], comment: metallb controller}
  - {port: 31100, sources: [core-cluster], comment: loki push NodePort}
```

## Newly asserted — loud where it used to be silent

These fail at role entry rather than provisioning something wrong. A `--check`
run exercises all of them.

| Role | Now asserted | Escape hatch |
|---|---|---|
| `adguard_home` | `adguard_home_dhcp_enabled: true` fails. The role only ever implemented the disable direction, so `true` was a silent no-op. | Set it false (the only value the role ever honoured). |
| `compose_app` | `compose_app_nginx_site_template` is a non-empty absolute path. | — |
| `encrypted_swap` | `encrypted_swap_source_device` is stat'd before anything is written to crypttab or fstab. | NEW `encrypted_swap_require_source_device` (default `true`) — set false to self-skip loudly instead of failing. The skip arm also REMOVES the crypttab entry, the mapper fstab line and the enabled finalize unit an earlier converge wrote, so a host that lost its backing device stops failing `systemd-cryptsetup@<mapper>` on every boot. The plaintext backing fstab line is left alone. |
| `immich` | `immich_nginx_real_ip_from` must resolve non-empty; it used to emit no `set_real_ip_from` at all. | Point `immich_nginx_real_ip_groups` at your own proxy group, set `_real_ip_from` directly, or set NEW `immich_nginx_trust_no_proxy: true`. |
| `k3s` | Every member of `k3s_server_group` names the same `k3s_kube_vip_interface`, and the evaluating host must BE a member of that group — a misnamed group resolves to `[]`, and an empty set agrees with itself. The DaemonSet is rendered once and runs on all servers, so a per-host override was silently ignored. | Converge a mixed-NIC control plane on one interface name, and point `k3s_server_group` at the group that actually holds the servers. |
| `proxmox_vm` | The memory reconcile FAILS instead of shrinking a live guest's allocation. | NEW `proxmox_vm_memory_shrink_ok` (default `false`) for a deliberate downsize. |
| `proxmox_vm` | `proxmox_vm_disk_size` matches `^[0-9]+[Gg]?$` on the WINDOWS create path — that boot disk is allocated as a bare GiB count. The Linux path still accepts M/G/T. | — |
| `restic_offsite` | `restic_offsite_repo_password` is non-empty, and `_b2_key_id`/`_b2_application_key` are non-empty when the remote type is `b2`. Both `no_log`. | — |
| `restic_offsite` | `restic_offsite_zvol_sources` repeats neither a `zvol` nor a `name`. | — |
| `zvol_mount` | `zvol_mount_disks` is defined and non-empty (previously a raw undefined-variable error). | — |

## Changed defaults and behaviour

Nothing to declare, but the deploy behaves differently. Grouped by whether it
can surprise you.

**Semantics that moved:**

| Role | Change |
|---|---|
| `adguard_home` | An empty `adguard_home_rewrites` / `_user_rules` now means "manage none" in fact as well as in the docs: the reconcilers skip instead of deleting every live record. Removing the LAST rewrite or rule through codification now needs NEW `adguard_home_prune_rewrites` / `_prune_user_rules` (default `false`). |
| `apt_signed_repo` | `apt_signed_repo_keyring_mode` default moves from `""` (skip the permission task, keyring left at gpg's umask-dependent mode) to `"0644"`. A keyring that ended up 0600 is corrected on the next run — expect one `changed` per host. The now-redundant explicit `0644` was dropped from `docker_engine` and `gitlab`. |
| `base` | `base_is_virtual_machine` is now `virtualization_role == 'guest'` (any hypervisor) and gates unattended-upgrades only. The old KVM-only expression lives on as NEW `base_is_kvm_guest`, which gates qemu-guest-agent. A VMware/Xen/Hyper-V/cloud guest now gets unattended-upgrades disabled, as the README always claimed; KVM guests are unaffected. |
| `base` | `base_ssh_password_authentication` / `_pubkey_authentication` are `\| bool`-coerced in defaults. A site passing the STRING `"false"` previously rendered `PasswordAuthentication yes` while the lockout guard believed it was off; it now renders `no`. Real booleans are unaffected. |
| `k3s` | Three opt-in features now CONVERGE ON OPT-OUT. `k3s_etcd_snapshot_offnode_enabled: false` stops and disables the copy timer, removes the NFS mount and deletes the units/script; `k3s_metrics_server_override_enabled: false` removes the manifest; `k3s_audit_enabled: false` removes the audit policy. A flag flip used to leave all three running. |
| `k3s` | The agent-token reconcile reads `k3s_token \| default('')`, so a site that scopes `k3s_token` to the server group no longer dies mid-play on an undefined variable. The agent preflight's `fail_msg` now names `k3s_agent_token`, the variable it actually checks. |
| `nas_storage` | The metric scripts (`archive-backupctl`, `media-mover`, `swap-clean`) resolve their textfile dir from `nas_storage_backup_artifact_metrics_dir` instead of hard-coding `/var/lib/node_exporter`, and mkdir it before writing. A site that moved the textfile dir was silently losing those three metric sets. |
| `nas_storage` | smbd stops binding TCP/139 — NEW `nas_storage_samba_ports` (`445`) and `nas_storage_samba_disable_netbios` (`true`). Widen the port list only for a client that cannot speak SMB over 445. |
| `proxmox_firewall` | The TCP 60000-60050 cleartext-migration rules in sg-pve-cluster and sg-host-egress are now opt-in behind NEW `proxmox_firewall_insecure_migration_ports` (default `false`), because `proxmox_ha` pins `migration: type=secure`. |
| `proxmox_ha` | The role reconciles datacenter.cfg's `migration:` key via `pvesh set /cluster/options`. PVE's own default is already secure, so this PINS rather than changes behaviour — but it WILL write datacenter.cfg on first run for a cluster that never declared the key, and it reverts a live `insecure`. NEW `proxmox_ha_migration_type` (default `secure`; `""` leaves the key unmanaged) and `_migration_network` (empty carries the live network through, since the property string is replaced wholesale). |
| `proxmox_ha` | Orphan HA rules and resources are now REPORTED as warnings, matching replication. Nothing is ever deleted; the warning names the manual `ha-manager` command. |
| `proxmox_vm` | The cloud-init assert moved after the existence probe and now carries the same `proxmox_vm_exists.rc != 0` gate as the tasks it protects, so a reconcile-only run against an existing guest no longer fails on gateway/DNS values it never reads. |
| `restic_offsite` | The zvol clone name is now `<zvol>-<suffix>` (was `<parent-of-zvol>-<suffix>`), so two sources under one parent no longer collide. **A stale clone left at the OLD name by a crashed pre-upgrade run is not cleaned up by the EXIT trap — destroy it by hand.** |
| `restic_offsite` | Restore-drill selection is rebuilt: candidates are bucketed per file source and drawn round-robin instead of taking the head of a globally size-sorted list, below-floor candidates are skipped, and the drill logs a per-source breakdown. `restic_offsite_restore_drill_max_bytes` default rises 8 -> 16 MiB to pay for the spread. NEW `_restore_drill_min_bytes` (4096) and `_restore_drill_min_sources` (1, clamped to the number of configured file sources so it cannot wedge; only file sources count, zvol sources have no comparand). |
| `acme_certs` | `Le_ReloadCmd` is reconciled on every run. On a host whose cert arrived by another route the first converge re-runs `--install-cert`, which triggers one distribution pass (the explicit distribution task is suppressed that run, so nothing is pushed twice). |
| `acme_certs` | `homelab-cert-reload.sh` no longer emits `<HOST>_IP` / `<HOST>_CERT_DIR` shell variables — the IP and cert_dir are passed positionally. This fixes FQDN targets (which rendered an invalid assignment and broke distribution to EVERY target) and two hosts differing only by `.` vs `-` colliding on one variable. Anything grepping the deployed script for those names must be updated. |
| `nfs_tls` | NEW `nfs_tls_scrub_client_cert` (default `true`, today's behaviour). Set false when another role owns `nfs_tls_cert_path`/`_key_path` — the defaults are also `acme_certs`' local install path, and the two roles would otherwise delete and reinstall the same files on alternate converges. |
| `plex` | non-free is enabled by normalising the deb822 `Components:` line (the same mechanism `k3s/tasks/gpu.yml` uses, via NEW `plex_debian_sources_path`), falling back to one-line entries only on a pre-deb822 host; the superseded one-line entries are removed. |
| `qol` | The whole role is gated on `os_family == Debian` (previously only the package install was, so a shell change could outrun it). Dotfile paths resolve from passwd rather than assuming `/home/<user>`. |
| `unbound` | The readiness probe digs `@unbound_interface` instead of a hardcoded 127.0.0.1, so moving the listen address no longer breaks it. NEW `unbound_probe_name` (default `google.com`) — a host without public egress must point it at a name its forwarders answer. |
| `zfs_encryption` | No behaviour change. `zfs_encryption_connect_vault` still defaults to `Homelab`; the README now carries a "Scoping the Connect token" section with the ordering constraint (the Connect server must serve the vault BEFORE the token is scoped to it) and the wedged-boot failure mode. Re-scoping is a live sequence, not a bump. |

**One-off writes and restarts** — real changes to live state on the first
converge after the bump, none of them behavioural. Budget for them in the deploy
plan rather than reading them as drift:

| Role | What moves | Consequence |
|---|---|---|
| `home_assistant` | configuration.yaml loses three restating comments. | The role sha256-compares against the deployed file, so it pushes once and runs `ha core check`. |
| `immich_ml` | The deployed compose file loses its meta-comment, the site-specific asides and the VRAM narrative (all moved to the README). | Notifies "Restart compose stack" — a full `docker compose down`/up of the ML stack. Budget one ML outage window. |
| `immich` | `immich_metrics_bind` (NEW, default `0.0.0.0` = today's binding) is prefixed onto all three published metrics ports, so the port strings render as `0.0.0.0:8081:8081`. | One stack restart. The binding is unchanged; the seam is there so a site that does not scrape from off-host can pin loopback in one place. |
| `nas_storage` | smartd.conf renders per-group comments instead of the four pool-named headers. | One smartd restart. Same disks, same `-s` schedules, `-o`/`-S` still dropped for the NVMe group. |
| `nas_storage` | `nfs-server-zfs.conf` loses its dated incident narration. | A deliberate one-time nfsd bounce. The handler's stop path can hang under live NFS clients (it is async/poll-bounded). |
| `vfio_passthrough` | Three rendered templates change comment text. | Fires the GRUB/initramfs rebuild handlers and the reboot-required warning once on an enabled GPU host. The binding is unchanged — no reboot is actually required for this release. |

## New variables (defaults preserve today's behaviour)

Per [EXTENSIBILITY.md](../../../docs/EXTENSIBILITY.md), a seam defaults to
current behaviour and needs no action. They are listed because several are the
escape hatch for an assert above, and because a consumer whose backends differ
from weisssrv's is why they exist.

| Role | Variable | Default | What it unlocks |
|---|---|---|---|
| `acme_certs` | `acme_certs_textfile_dir` | `node_exporter_host_textfile_dir \| default('/var/lib/node_exporter')` | A moved textfile dir, set once. |
| `adguard_home` | `adguard_home_web_bind`, `_dns_bind` | `0.0.0.0` | The addresses the first-install setup wizard binds. No change for existing hosts — the wizard only runs on a fresh install. |
| `adguard_home` | `adguard_home_after_units`, `_wants_units` | `[unbound.service]` | Set them empty when pointing `adguard_home_upstream_dns` at a public resolver. The unit renders byte-identically at the default. |
| `adguard_home` | `adguard_home_dns_probe_name` | `google.com` | The name the post-deploy dig smoke test resolves. |
| `adguard_home` | `adguard_home_prune_rewrites`, `_prune_user_rules` | `false` | Codified removal of the LAST rewrite or rule (see the reconcile gate above). |
| `adguard_sync` | `adguard_sync_replicas` | `[]` | A list of `{url, username, password}` (credentials falling back to `adguard_sync_admin_user`/`_password`) rendering upstream's `replicas:` block. When non-empty, `adguard_sync_replica` is ignored; it is required only while this is empty. |
| `adguard_sync` | `adguard_sync_textfile_dir` | as `acme_certs` above | — |
| `base` | `base_ssh_authorized_keys_exclusive` | `false` (additive, as today) | Makes `ssh_authorized_keys` authoritative so a removed key is revoked. It also removes keys installed outside Ansible. The whole list now ships in ONE `authorized_key` call (a looped `exclusive: true` would leave only the last key). |
| `base` | `base_is_kvm_guest` | KVM-guest detection | qemu-guest-agent, split out of `base_is_virtual_machine` (above). |
| `home_assistant` | `home_assistant_enable_prometheus`, `_enable_default_config`, `_tts_platforms`, `_includes` | today's values | Emptying `_includes` or `_tts_platforms` omits the block — what a consumer whose HAOS lacks the `!include` targets needs. |
| `immich` | `immich_metrics_bind` | `0.0.0.0` | Pinning the metrics ports to loopback in one place. |
| `immich` | `immich_nginx_trust_no_proxy` | `false` | Opting out of the real-IP assert. |
| `k3s` | — | — | (no new seams; see the opt-out convergence above) |
| `nas_storage` | `nas_storage_zfs_mount_roots` | `[/mnt/tank, /mnt/ssd, /mnt/nvme]` | Replaces a hard-coded `^/mnt/(tank\|ssd\|nvme)/` regex that gated BOTH the mounted-dataset guard and the fstab zfs-mount ordering, plus the derived `nas_storage_zfs_bind_source_pattern`. A site whose datasets live elsewhere MUST set it — both protections silently did nothing there. |
| `nas_storage` | `nas_storage_samba_password` | `lookup('env', 'SAMBA_NAS_PASSWORD', default='')` | samba.yml no longer reads the environment inline, so Vault/SOPS/ansible-vault can supply it. No change for `op run --` consumers. |
| `nas_storage` | `nas_storage_smartd_disk_groups` | the four pool groups | `{name, disks, schedule, ata?}`, replacing the four fixed pool-named lists in the template and the coverage assert. The legacy `nas_storage_smartd_{tank,ssd,nvme,archive}_disks` survive as the default groups' inputs, so existing inventories are unchanged; a different pool layout sets the groups directly instead of forking the template. |
| `node_exporter_host` | `node_exporter_host_corosync_collector` | `node_exporter_host_proxmox` | Set false on a standalone (non-clustered) PVE host so the collector is not deployed at all — it would otherwise publish mtime 0, which `PmxcfsStale` treats as stale by design. Turning it off also reconciles a previously deployed collector away. |
| `proxmox_backup` | per-entry `pool`, `nodes`, `mountpoint`, `sparse` | — | `type: zfspool` entries. `pool` joins server/export/path in the create-fixed drift assert (its drift is what defeats at-rest encryption); nodes/mountpoint/sparse join the mutable reconcile, where an undefined desired value inherits the live one rather than clearing it. |
| `proxmox_lxc` | `proxmox_lxc_netmask_bits` | `24` | Interpolated into `pct create --net0`. Any LAN that is not a /24 MUST set it (`proxmox_vm_cloudinit_prefix_len` is the VM counterpart). Create-time only. |
| `restic_offsite` | `restic_offsite_rclone_remote_name`, `_rclone_remote_type`, `_rclone_remote_options` | `b2` / `b2` / `{}` | rclone.conf renders the b2 account/key only for the b2 type; any other type takes all settings from the options map, rendered verbatim as `key = value`. |
| `qol` | `qol_admin_home`, `qol_admin_shell` | `""` (resolve from passwd), `/bin/zsh` | Set `qol_admin_home` only when the home cannot be looked up. |

`nas_storage` also gained the task files `mergerfs_needs_remount.yml` and
`mergerfs_remount_gate.yml`, extracted verbatim from `mergerfs.yml` so the
remount decision facts are testable without FUSE. No variable or behaviour
change.

## Library surfaces outside the collection

The collection is not the only pinned surface. These move in the same release.

**New — `ci/deploy/`.** `deploy-base.yml` (`.deploy-base`), `kubectl-setup.yml`
and `ansible-deploy.yml`, extracted from the two cluster pipelines. `op_vault`
is REQUIRED on the first two (a vault name is site data, not a library
default), and `job_name` / `needs` / `resource_group` / `environment_name` /
`changes` are REQUIRED on `ansible-deploy` — `needs` above all, because the only
value the library could default it to is `[]`, which in GitLab starts the job at
pipeline creation and bypasses every gate. `deploy-base` sets `LOKI_PUSH_USER`/`_PASSWORD` on
the base — closing an observed drift — so the Loki item must exist in `op_vault`
for every job that extends it. The per-job secret map is a same-name map-merge
on the consumer side, not an input. The cluster template adopts `deploy-base`
in this release; `kubectl-setup` and `ansible-deploy` had no consumer yet and
were registered as `not_yet_adopted` in the then-current `docs/CONSUMERS.yml`
(retired in v0.9.0 with the registry inversion).

**New — `ci/github/`.** `ci.example.yml` and `build-image.example.yml`, promoted
to published vendorable references now that the CLI fixtures that carried them
are gone. The example lints `scripts` and kubeconforms `kubernetes/flux` alone —
the optional manifests that used to sit in a second directory are copier-gated
files, and a rendered tenant carries no test suite. The library is canonical for
these two and for `ci/release/github-release-workflow.example.yml`; their
`docs/CI-SHAPES.md` pointers are qualified "(app template)" because that doc
lives in the template repo and NOT in the rendered tenant they are vendored
into. Whether any of them needs re-vendoring at bump time is a question for
`scripts/check-vendored-copies.py --consumer weisssrv-app-template`, which names
exactly the files that drifted — this paragraph deliberately states no count,
because a claim about another repo's tree cannot stay true across the release
window. Registry paths for that consumer follow its copier layout (`template/…`,
jinja conditional directory names included), and `build-image.example.yml`'s is
gated on `enable_image_build` as well as the shape: the GitLab shape already
gated its own build job on that answer, so an unconditional GitHub workflow left
the two shapes disagreeing about what `enable_image_build: false` means and gave
a tenant that never builds an image a `packages: write` workflow on every push.
Both GitHub workflows also ANNOUNCE their no-Dockerfile skip (a `::warning::`
and a step-summary line) instead of exiting green in silence — a byte-identical
file cannot tell "runs an upstream image" from "the Dockerfile was renamed".

**Terraform modules — `authentik-sso` grows three capabilities.** All three are
additive; a caller that passes nothing new renders the same objects, with one
behaviour change to check on the first plan.

- **NEW `custom_scope_mappings`.** Scope property mappings the module AUTHORS,
  keyed by an identifier and referenced from `oauth2_scope_mappings` or a
  provider's `scope_mappings` as `custom:<key>`, interleaved with managed ids in
  one ordered list. This is what an application that refuses a login over a
  claim authentik does not emit by default needs (the stock `email` scope
  hardcodes `email_verified: false`); until now the mapping had to be created in
  the UI and then showed up as permanent drift.
- **NEW `applications[*].allow_unbound`, and an unbound application now FAILS
  the plan.** Every `authentik_application` carries a `precondition` asserting
  some ENABLED `policy_bindings` entry names its slug — an unbound application is
  reachable by every authenticated user, and forgetting one used to produce a
  perfectly valid plan. A binding with `enabled = false` does not count, because
  the policy engine never evaluates it, so suspending an application's last
  binding fails the plan instead of quietly opening the app. A caller with a
  deliberately open tile sets `allow_unbound = true` on it; a caller with an
  accidental one has a real finding to fix. This is the arm that reaches a
  consumer passing no new input, and it is also listed under
  [Breaking](#breaking--act-in-the-same-mr-as-the-bump).
- **`prevent_destroy` on applications, all three provider kinds, groups, the
  custom mappings and the embedded outpost.** Unconditional, not a per-object
  flag like `cloudflare-zone`'s — a flag has to route the object to a second
  resource address, and an address change here IS the destroy+create it would be
  protecting against. Consequence: removing an object is now
  `terraform state rm 'module.<name>.<resource>.this["<key>"]'` then the map
  entry then the object in authentik, and setting `embedded_outpost` back to
  null is refused rather than silently destroying authentik's own outpost.
  Renames are unaffected — `moved {}` is not blocked by `prevent_destroy`.

One quieter change with the same intent: a group with no attributes now gets
`attributes = null` instead of `jsonencode({})`, so the module stops asserting an
empty object on groups whose attributes it does not manage (an adopted group
carrying attributes no longer plans as a wipe).

**Changed CI template inputs:**

| Template | Change |
|---|---|
| `ci/review/pr-agent.yml` | `op_openai_key_ref` and `op_gitlab_token_ref` no longer default to `op://Homelab/...`; they default to `""` and are REQUIRED when `secrets_source: 1password` (the job exits 1 naming the missing input). Consumers on `secrets_source: env` are unaffected. **Security note for the release: the GitLab credential on EITHER path must be a project access token with Developer + `api` on the reviewed project, never an instance or admin PAT.** |
| `ci/templates/terraform-http-backend.yml` | `api_url` default moves from a literal instance URL to `${CI_API_V4_URL}`. Same value on that instance, so no consumer's rendered `TF_HTTP_*` changes; a consumer that was overriding it can drop the override. |
| `ci/templates/dep-cache.yml` | Now a `spec:inputs` template (was a plain fragment). NEW `key_files` and `cache_paths`, both defaulting to today's hard-coded values, so the render is byte-identical. A consumer whose pin files are not `requirements.txt` / `ansible/requirements.yml` passes its own `key_files`. |
| `ci/build/docker-build.yml` | NEW `login_registry` / `login_user` / `login_password`, defaulting to the `$CI_REGISTRY` trio the job used to hard-code. NEW `schedule_when` (`on_success` \| `never`, default `on_success`) makes the scheduled rebuild opt-out-able. Every build now applies OCI provenance labels (`org.opencontainers.image.{source,revision,version,title}`) with `--label`, which overrides a same-key `LABEL` in a consumer's Dockerfile. |
| `ci/validate/terraform.yml` | `terraform-validate` now FAILS when `module_glob` matches no directory containing a `versions.tf` (previously a green no-op). A consumer whose glob was wrong goes red — point it at the level that holds the modules. |
| `ci/lint/shellcheck.yml` | A failure in one of the three script blocks no longer exits immediately; all three run and the final accounting block exits. Pass/fail is unchanged, output is more complete. |

**New library scripts.** Six cluster-invariant gates are promoted out of
weisssrv and now ship here, parameterised so they are not weisssrv-shaped:
`check-pvc-storageclass.py`, `check-secretstore-scope.py`,
`check-scrape-netpol.py`, `check-netpol-except-parity.py`,
`check-alertmanager-behaviour.py` and `check-backup-artifact-apps.py` (each with
the required flags listed under Breaking above). **None of them is drop-in.**
`check-pvc-storageclass.py` and `check-secretstore-scope.py` take no new flags,
but both gained the exit-code contract in the next section, so diff your local
copy against the library's before deleting it. Two example configs ship with
them:
`examples/netpol-except.example.yaml` and
`examples/alertmanager-behaviour.example.yaml`.

**New vendored-copy registry.** `scripts/vendored-paths.yml` records every
library file copied into a consumer — per consumer, split into `vendored`
(byte-identical) and `forked` (deliberately divergent, with the library-side sha
they were last reconciled against) — and `scripts/check-vendored-copies.py` is
the gate a consumer runs against a library checkout. It reaches past `scripts/`
to the lint profiles, the vendored `.github/workflows/*` and
`tests/test_check_lib_pins.py`, which the three consumer-local gates do not.
Consumers should replace their hand-maintained lists with a call to it; the
registry records the TARGET state, so each consumer's gate is red until its own
adoption MR lands.

**Gates that no longer pass on nothing.** Five gates used to report green on an
input they never inspected — four promoted ones plus the new
`check-vendored-copies.py`; each now exits **2** on that shape, so an adopting
consumer must point them at real data. This is the arm
that reaches a caller passing no new flag at all — a `kustomize build` that
renders nothing, or a corpus that fails to arrive, reds the job where it used to
pass. Exit 1 keeps its old meaning ("the invariant is violated"), so a wrapper
that treats any non-zero as a finding will misreport these:

- `check-pvc-storageclass.py`: an EMPTY corpus is an operator error, and the
  YAML-parse arm moved from exit 1 to exit 2. So is a corpus that ARRIVED but
  declares no claim at all — no PVC, no `volumeClaimTemplate`, no chart
  persistence block that sizes a volume — which is what a render loop that never
  reached the storage-declaring stages produces. The success line reports the
  claim count next to the document count.
- `check-scrape-netpol.py`: same empty-corpus contract, and a new
  `OperatorError` carries BOTH the YAML-parse arm and a malformed `--exempt`
  from exit 1 to exit 2. A corpus that arrived but holds NO scrape target is the
  same operator error, for the same reason: the observability stage never
  rendered, so every namespace went unexamined. Scrape targets with none
  ingress-restricted among them is still a pass — default-deny is a per-namespace
  choice — and both counts are on the success line.
- `check-secretstore-scope.py`: an EMPTY corpus is an operator error, and a
  `ClusterSecretStore` that is referenced but not defined in the corpus is now a
  VIOLATION rather than a note — that reference is exactly the runtime failure
  the gate exists to catch. A store genuinely managed outside the linted tree is
  declared with the new repeatable `--external-store NAME`. Also, a
  `ClusterExternalSecret` with `namespaceSelector: {}` now correctly fans out to
  every namespace instead of being skipped.
- `check-netpol-except-parity.py`: a run that inspects ZERO NetworkPolicy
  documents, or is pointed at a path that does not exist, is an operator error
  (the latter used to be an uncaught traceback). A scanned manifest that does
  not parse is the same class — it used to be reported as a drifted except-list
  on exit 1. The success line now reports the count scanned.
- `check-vendored-copies.py`: a missing library checkout exits 2, not 1, and the
  `--ref` working-tree fallback is decided per REF rather than per path — a file
  the library added after the pinned tag is reported as not shipped by that
  release instead of being compared against a newer tree.

`check-alertmanager-behaviour.py` changes three behaviours in the same spirit:
the resolved receiver is compared exactly against amtool's first output token (a
prefix test passed `critical-page` for an expected `critical`), `--repo-root`
is now the extractor's cwd as well, so the gate runs from any directory, and the
extracted config and rules are parsed ONCE up front. That last one matters
because the extractor copies the `alertmanager.yaml` block scalar out of the
ExternalSecret without parsing it: a YAML typo inside that block left the outer
manifest valid, the extractor green, and the malformed body arriving mid-check
as an uncaught traceback on exit 1. It is now an operator error (exit 2).

`examples/netpol-except.example.yaml` now ships BOTH canonical except-lists:
declaring `canonical_except_lists` replaces the built-ins wholesale, so the
one-set example silently retired `reserved-full` for anyone who copied it.

**Other script behaviour:**

- `scripts/check-versions.py`: NEW `report_title` config key (default
  `"Version Check Report"`). The table heading is no longer hard-coded to
  `"Homelab Version Check Report"` — a consumer that wants the old heading sets
  the key.
- `scripts/generate-hosts-env.py`: a `group:` naming a group-of-groups now
  resolves to the union of its descendants instead of raising. The error wording
  for an empty required export changed from `(<target> renamed/removed?)` to one
  of three distinct causes; a consumer asserting on the old string must update.

## How to check a migration

```bash
# 1. Every old name still set anywhere in your inventory:
grep -rnE '^\s*(adguard_|fail2ban_|lxc_|vm_|pve_|ha_|smtp_|nas_|acme_|dns01_|omz_|nvim_|media_|smartd_|zfs_scrub_|restic_|rclone_|b2_|storage_replication_|cloudinit_|cloud_image_|virtio_win_|skip_)' \
  ansible/inventories/

# 2. Nothing in the collection reads it — prove the rename landed:
ANSIBLE_COLLECTIONS_PATH=$PWD:~/.ansible/collections \
  ansible-playbook site.yml --check --diff --limit <one-host>
```

A `--check` run exercises every **required-input assert** (see the last section),
which is the loud half of the contract. The silent half — a renamed *tunable*
that falls back to a role default — is only caught by diffing rendered config,
so diff one host's rendered files (or `pve-firewall compile`, `sshd -T`,
`unbound-checkconf`) before and after adoption.

Neither step finds the third class: a variable whose **name is unchanged** but
whose weisssrv-specific default is now empty. The grep has no old prefix to match
and a defaults diff shows the key on both sides. Those are enumerated in
[Externalized defaults](#externalized-defaults-name-unchanged-value-now-empty)
below — work that table on its own.

## Names that do NOT need renaming

Values that are conventionally inventory-wide keep their bare names; the roles
alias them with a `default()`. Setting either the bare or the prefixed form
works. The table lives in [README.md](README.md#use) — currently `admin_user`,
`admin_email`, the `ssh_*` quintet, `timezone`, `dns_servers`, `internal_domain`,
`external_domain`, `zfs_arc_max_bytes`, `host_dns_servers`,
`vm_additional_disks`, `redis_version`, `immich_version`, the `kube_vip_*` pair
and the four `nvidia_*` GPU pins.

Two consequences worth stating explicitly:

- `admin_user`, `timezone`, `ssh_port`, `ssh_permit_root_login`,
  `ssh_password_authentication`, `ssh_pubkey_authentication`,
  `zfs_arc_max_bytes` and `internal_domain` appear in the per-role tables below
  **because the role-owned name changed**, but the bare name still works through
  the alias. They are the only rows you may skip.
- `vm_additional_disks` is read by both `proxmox_vm` (creates and attaches the
  zvols) and `k3s` (mounts them), so one `host_vars` block drives both.

## Externalized defaults (name unchanged, value now empty)

The per-role tables below only record **renames**. This section records the other
half: variables that kept their name while their *value* changed from a
weisssrv-specific default to an empty one the site must now supply. Nothing looks
renamed, so the grep recipe above returns nothing and a defaults diff shows the
key on both sides — and most of these are not asserted, so the play stays green
while the behaviour degrades quietly (an offsite backup with no paths, a
root-equivalent key with no source pin, a `/etc/hosts` pin that never lands).

The table is generated mechanically: for every role, each key present in **both**
defaults files whose weisssrv value was non-empty and whose collection default is
`""` or `[]`.

A second, nastier variant is **renamed _and_ emptied**: the rename tables below
tell you the new name, so the grep recipe finds it, but they say nothing about
the value that disappeared with it. Both halves are required.

| Role | Old (inventory) | New | weisssrv value to restore |
|---|---|---|---|
| `acme_certs` | `acme_email` | `acme_certs_email` | the ACME account address |
| `acme_certs` | `internal_domain` | `acme_certs_domain` | the internal zone |
| `adguard_home` | `adguard_tls_server_name` | `adguard_home_tls_server_name` | the DoT server name |
| `nas_storage` | `nas_appdata_dirs` | `nas_storage_appdata_dirs` | the 11 per-app appdata subdirs |
| `nas_storage` | `nas_backup_artifact_apps` | `nas_storage_backup_artifact_apps` | the 6 apps whose dumps are freshness-tracked |
| `restic_offsite` | `restic_version` | `restic_offsite_restic_version` | the pinned restic version (empty in weisssrv's `all.yml` today, meaning "track the distro" — either pin it or delete the key rather than shipping an empty pin into `cluster-versions`) |
| `restic_offsite` | `rclone_version` | `restic_offsite_rclone_version` | the pinned rclone version (paired with `restic_offsite_rclone_deb_sha256`) |

| Role | Variable | New default | Asserted | Effect if left empty |
|---|---|---|---|---|
| `acme_certs` | `acme_certs_key_from` | `""` | no | no `from="…"` clause on the distribution key in each target's `authorized_keys` — the root-equivalent key becomes usable from any source address |
| `alloy_host` | `alloy_host_loki_url` | `""` | yes | role fails at entry |
| `alloy_host` | `alloy_host_loki_user` | `""` | `https://` only | push runs unauthenticated |
| `alloy_host` | `alloy_host_loki_password` | `""` | `https://` only | push runs unauthenticated |
| `compose_app` | `compose_app_nginx_self_signed_san` | `""` | no | the placeholder cert is generated with no `subjectAltName` |
| `k3s` | `k3s_api_vip` | `""` | yes | role fails at entry |
| `k3s` | `k3s_etcd_snapshot_nfs_server` | `""` | no | the off-node snapshot mount has no server half (read only when `k3s_etcd_snapshot_offnode_enabled`) |
| `k3s` | `k3s_registry_host_pins` | `[]` | no | no `/etc/hosts` pin for the registry — image pulls fall back to cluster DNS |
| `k3s` | `k3s_storage_host_pins` | `[]` | no | no `/etc/hosts` pin for the NFS server — PV mounts fall back to cluster DNS |
| `restic_offsite` | `restic_offsite_repo` | `""` | yes | role fails at entry |
| `restic_offsite` | `restic_offsite_sources` | `[]` | no | the nightly `restic backup` runs with an empty path set |
| `restic_offsite` | `restic_offsite_zvol_sources` | `[]` | no | zvol-backed data (Immich, Nextcloud) is never clone-mounted, so it is never offsited |
| `restic_offsite` | `restic_offsite_excludes` | `[]` | no | churn/cache paths (Prometheus, Loki, the Plex cache) ride into the repo |

The weisssrv values, ready to move into inventory:

```yaml
# acme_certs — the dns-01 resolver the distribution key is pinned to
acme_certs_key_from: 192.168.0.150

# alloy_host — the credentials were env lookups in the role's defaults
alloy_host_loki_url: https://loki.esweiss.com/loki/api/v1/push
alloy_host_loki_user: "{{ lookup('ansible.builtin.env', 'LOKI_PUSH_USER') | default('', true) }}"
alloy_host_loki_password: "{{ lookup('ansible.builtin.env', 'LOKI_PUSH_PASSWORD') | default('', true) }}"

# compose_app
compose_app_nginx_self_signed_san: DNS:*.esweiss.com

# k3s
k3s_api_vip: 192.168.0.161
k3s_etcd_snapshot_nfs_server: "pve-nas-01.{{ internal_domain | default('esweiss.com') }}"
k3s_registry_host_pins:
  - name: "registry.git.{{ external_domain | default('ericsweiss.com') }}"
    ip: 192.168.0.101
k3s_storage_host_pins:
  - name: "pve-nas-01.{{ internal_domain | default('esweiss.com') }}"
    ip: 192.168.0.102

# restic_offsite
restic_offsite_repo: rclone:b2:weisssrv-backup/restic
restic_offsite_sources:
  - name: backups
    mountpoint: /mnt/tank/backups
  - name: share
    mountpoint: /mnt/tank/share
  - name: appdata
    mountpoint: /mnt/ssd/appdata
  - name: databases
    mountpoint: /mnt/ssd/databases
  - name: k3s-etcd
    mountpoint: /mnt/ssd/k3s-etcd
restic_offsite_zvol_sources:
  - name: immich-data
    zvol: tank/immich-data/disk
    fstype: ext4
    mount_opts: ro,noload
  - name: nextcloud-data
    zvol: tank/nextcloud-data/disk
    fstype: ext4
    mount_opts: ro,noload
restic_offsite_excludes:
  - /mnt/restic-src/appdata/prometheus/**
  - /mnt/restic-src/appdata/loki/**
  - /mnt/restic-src/appdata/authentik/postgres/**
  - /mnt/restic-src/appdata/mealie/postgres/**
  - /mnt/restic-src/appdata/gitlab/**
  - /mnt/restic-src/appdata/immich/**
  - /mnt/restic-src/appdata/nextcloud/**
  - "/mnt/restic-src/appdata/plex/Library/Application Support/Plex Media Server/Cache/**"
  - "/mnt/restic-src/appdata/plex/Library/Application Support/Plex Media Server/Metadata/**"
  - "/mnt/restic-src/appdata/plex/Library/Application Support/Plex Media Server/Media/**"
```

### Same class, different name

Four more values were externalized *and* renamed (or promoted out of a template),
so they do appear in the tables below — they are listed here too because the
migration step is identical: supply the value or lose the behaviour.

- `nas_storage` **archive backup**: the dataset inventory was literal in
  `archive-backupctl.sh.j2` (`SRC_LIST`, `POOL_DST`, `VZDUMP_TARGET`) and is now
  `nas_storage_archive_backup_pool: archive`,
  `nas_storage_archive_backup_vzdump_target: tank/proxmox` and
  `nas_storage_archive_backup_sources: [tank/share, tank/backups,
  tank/nextcloud-data, tank/proxmox, tank/immich-data, ssd/appdata,
  ssd/databases, ssd/k3s-etcd]`, behind `nas_storage_archive_backup_enabled`
  (default false). Pool and sources are asserted when the opt-in is on; leaving
  the opt-in off on a host that already runs the timer **removes** the units and
  the script rather than orphaning them.
- `restic_offsite_cache_dir`: same name, but no longer a default at all — it is a
  required input, asserted alongside `restic_offsite_repo`. weisssrv's value was
  `/mnt/ssd/appdata/.restic-cache`.
- `k3s_tls_sans`: the apiserver SAN list was hardcoded as
  `k3s.{{ internal_domain }}`; it is now an input that defaults to
  `['k3s.' ~ k3s_internal_domain]` and collapses to `[]` when neither
  `k3s_internal_domain` nor the inventory-wide `internal_domain` is set. The VIP,
  `inventory_hostname` and `ansible_host` are still added by the template.
- `proxmox_firewall` address data: the CIDR lists and the seven per-application
  `[group ...]` blocks were literal in the template and are now empty-by-default
  inputs — see [proxmox_firewall](#proxmox_firewall) below for the full list.

## Per-role renames

Rows marked (inv) were never role defaults — they are names a site set directly
in `group_vars`/`host_vars`, so they will not show up in a defaults diff.

### acme_certs

| Old | New |
|---|---|
| `acme_email` | `acme_certs_email` |
| `acme_local_cert_group` | `acme_certs_local_cert_group` |
| `acme_sh_tarball_sha256` | `acme_certs_sh_tarball_sha256` |
| `acme_sh_version` | `acme_certs_sh_version` |
| `internal_domain` | `acme_certs_domain` (the cert's base domain — a dedicated required input, no longer the shared global) |
| `local_cert_dir` | `acme_certs_local_cert_dir` |
| `cert_distribution_targets` (inv) | `acme_certs_distribution_targets` |
| `dns01_ssh_private_key` (inv) | `acme_certs_ssh_private_key` (asserted) |
| `dns01_ssh_public_key` (inv) | `acme_certs_ssh_public_key` (asserted) |

`acme_certs_key_from` keeps its name but is now empty by default — see
[Externalized defaults](#externalized-defaults-name-unchanged-value-now-empty).
`skip_cert_distribution` → `acme_certs_skip_distribution`.

The role no longer gates itself on a hostname, so **`acme_certs_enabled: true`
replaces the `inventory_hostname == 'dns-01'` check**. Five more defaults are
generic where the in-tree role's were site values, and each silently changes
behaviour if left alone: `acme_certs_ssh_user` (default `root`, which also
relocates the key under `acme_certs_ssh_key_dir`),
`acme_certs_local_cert_dir` (`/etc/ssl/private`), `acme_certs_local_cert_group`
(`root`), and `acme_certs_local_reload_command` (**empty, which omits the local
service-restart block entirely**).

Two behaviour changes: the receiver now **rejects** an oversized bundle instead
of truncating it (a truncated PEM was reported as "certificate does not parse"),
and the per-target textfile keeps the name `cert_distribution_targets.prom` —
renaming it to match the variable prefix would leave the old file in place and
node_exporter would serve one metric family from two textfiles.

### adguard_home

| Old | New |
|---|---|
| `adguard_admin_user` | `adguard_home_admin_user` |
| `adguard_cache_enabled` | `adguard_home_cache_enabled` |
| `adguard_cache_optimistic` | `adguard_home_cache_optimistic` |
| `adguard_cache_size` | `adguard_home_cache_size` |
| `adguard_cache_ttl_max` | `adguard_home_cache_ttl_max` |
| `adguard_cache_ttl_min` | `adguard_home_cache_ttl_min` |
| `adguard_cert_path` | `adguard_home_cert_path` |
| `adguard_dhcp_enabled` | `adguard_home_dhcp_enabled` |
| `adguard_disable_ipv6` | `adguard_home_disable_ipv6` |
| `adguard_dns_port` | `adguard_home_dns_port` |
| `adguard_doq_port` | `adguard_home_doq_port` |
| `adguard_dot_port` | `adguard_home_dot_port` |
| `adguard_enable_dnssec` | `adguard_home_enable_dnssec` |
| `adguard_fallback_dns` | `adguard_home_fallback_dns` |
| `adguard_group` | `adguard_home_group` |
| `adguard_http_port` | `adguard_home_http_port` |
| `adguard_https_port` | `adguard_home_https_port` |
| `adguard_install_path` | `adguard_home_install_path` |
| `adguard_protection_enabled` | `adguard_home_protection_enabled` |
| `adguard_ratelimit` | `adguard_home_ratelimit` |
| `adguard_ratelimit_whitelist` | `adguard_home_ratelimit_whitelist` |
| `adguard_resolve_clients` | `adguard_home_resolve_clients` |
| `adguard_tls_enabled` | `adguard_home_tls_enabled` |
| `adguard_tls_server_name` | `adguard_home_tls_server_name` |
| `adguard_upstream_dns` | `adguard_home_upstream_dns` |
| `adguard_upstream_mode` | `adguard_home_upstream_mode` |
| `adguard_use_private_ptr_resolvers` | `adguard_home_use_private_ptr_resolvers` |
| `adguard_use_private_tmp` | `adguard_home_use_private_tmp` |
| `adguard_use_protect_system` | `adguard_home_use_protect_system` |
| `adguard_user` | `adguard_home_user` |
| `skip_adguard_api_config` | `adguard_home_skip_api_config` |
| `adguard_admin_password` (inv) | `adguard_home_admin_password` |
| `adguard_rewrites` (inv) | `adguard_home_rewrites` |
| `adguard_user_rules` (inv) | `adguard_home_user_rules` |

New gates with no predecessor: `adguard_home_is_primary` (the rewrite/filtering
API pass runs on the primary only) and `adguard_home_skip_resolv_conf_update`.
Also new: `adguard_home_hash_helper_path`
(`/usr/local/sbin/adguard-admin-hash.py`) and `adguard_home_settle_seconds` (0).

Three things to plan for:

- **`adguard_home_tls_server_name` is now ASSERTED** when
  `adguard_home_tls_enabled`. It was previously possible to post an empty
  DoT/DoH/DoQ SNI on every deploy with a green play. This is the one row in the
  table above that is more than a rename.
- **`adguard_home_admin_user` defaults to `admin`.** A site whose admin is named
  otherwise gets a loud failure (`no user named 'admin' in …AdGuardHome.yaml`),
  not a silent one — but it stops the deploy.
- **A new file lands on each resolver**: the admin-password helper at
  `adguard_home_hash_helper_path` (root:root 0755). The password now reaches it
  on **stdin** rather than through `environment:`, which Ansible prefixes onto
  the remote command string — so the plaintext no longer appears in
  `/proc/<pid>/cmdline`. The first converge should print `UNCHANGED` and restart
  nothing; a `CHANGED` means the stored password and the vault have diverged,
  and the handler serializes the restarts one resolver at a time.

### adguard_sync

| Old | New |
|---|---|
| `adguardhome_sync_features` | `adguard_sync_features` |
| `adguardhome_sync_schedule` | `adguard_sync_schedule` |
| `adguardhome_sync_origin` (inv) | `adguard_sync_origin` |
| `adguardhome_sync_replica` (inv) | `adguard_sync_replica` |
| `adguardhome_sync_version` (inv) | `adguard_sync_version` |
| `adguard_admin_user` (inv) | `adguard_sync_admin_user` |
| `adguard_admin_password` (inv) | `adguard_sync_admin_password` |

The role is now gated on `adguard_sync_enabled` (default false) — set it true on
the primary only.

### alloy_host

No renames. Four values that used to default to site data are now **required
inputs** with an empty default: `alloy_host_version`, `alloy_host_loki_url`,
`alloy_host_loki_user`, `alloy_host_loki_password` (the last two only for an
`https://` endpoint). The env lookups that used to sit in the role's defaults
(`LOKI_PUSH_USER` / `LOKI_PUSH_PASSWORD`) move to the caller. The three that were
role defaults are listed with their weisssrv values under
[Externalized defaults](#externalized-defaults-name-unchanged-value-now-empty);
`alloy_host_version` was always inventory-supplied.

### base

| Old | New |
|---|---|
| `admin_user` | `base_admin_user` (alias: `admin_user`) |
| `common_packages` | `base_common_packages` |
| `fail2ban_default_bantime` | `base_fail2ban_default_bantime` |
| `fail2ban_default_findtime` | `base_fail2ban_default_findtime` |
| `fail2ban_default_maxretry` | `base_fail2ban_default_maxretry` |
| `fail2ban_email_action` | `base_fail2ban_email_action` |
| `fail2ban_email_dest` | `base_fail2ban_email_dest` |
| `fail2ban_email_enabled` | `base_fail2ban_email_enabled` |
| `fail2ban_email_sender` | `base_fail2ban_email_sender` |
| `fail2ban_enabled` | `base_fail2ban_enabled` |
| `fail2ban_ignoreip` | `base_fail2ban_ignoreip` |
| `fail2ban_pveproxy_bantime` | `base_fail2ban_pveproxy_bantime` |
| `fail2ban_pveproxy_enabled` | `base_fail2ban_pveproxy_enabled` |
| `fail2ban_pveproxy_findtime` | `base_fail2ban_pveproxy_findtime` |
| `fail2ban_pveproxy_maxretry` | `base_fail2ban_pveproxy_maxretry` |
| `fail2ban_pveproxy_port` | `base_fail2ban_pveproxy_port` |
| `fail2ban_recidive_bantime` | `base_fail2ban_recidive_bantime` |
| `fail2ban_recidive_enabled` | `base_fail2ban_recidive_enabled` |
| `fail2ban_recidive_findtime` | `base_fail2ban_recidive_findtime` |
| `fail2ban_recidive_maxretry` | `base_fail2ban_recidive_maxretry` |
| `fail2ban_sshd_bantime` | `base_fail2ban_sshd_bantime` |
| `fail2ban_sshd_enabled` | `base_fail2ban_sshd_enabled` |
| `fail2ban_sshd_findtime` | `base_fail2ban_sshd_findtime` |
| `fail2ban_sshd_maxretry` | `base_fail2ban_sshd_maxretry` |
| `fail2ban_sshd_port` | `base_fail2ban_sshd_port` |
| `ssh_password_authentication` | `base_ssh_password_authentication` (alias) |
| `ssh_permit_root_login` | `base_ssh_permit_root_login` (alias) |
| `ssh_port` | `base_ssh_port` (alias) |
| `ssh_pubkey_authentication` | `base_ssh_pubkey_authentication` (alias) |
| `ssh_service_name` | `base_ssh_service_name` |
| `timezone` | `base_timezone` (alias: `timezone`) |
| `vm_packages` | `base_vm_packages` |

New: `base_ssh_authorized_keys` (alias `ssh_authorized_keys`), the
`base_skip_{ssh,dns,timezone}_config` / `base_skip_sudoers_validation` gates, and
the resolver-host knobs `base_is_resolver_host` / `base_resolver_probe_name` /
`base_bootstrap_dns_servers`. `base_is_resolver_host` replaces the role's
`inventory_hostname in groups['dns']` check — set it `true` in the resolver
group. The `is_container` / `is_virtual_machine` set_facts are now
`base_is_container` / `base_is_virtual_machine`; nothing outside `base` reads
them.

Three behaviour changes to plan for:

- **`base` no longer installs the e1000e TSO workaround, and actively REMOVES
  it.** The in-tree role auto-detected I219/I218/I217 on any bare-metal host and
  installed `/usr/local/sbin/e1000e-tso-fix.sh` plus a oneshot unit; this role
  disables and deletes that pair (and the older `atlantic-gro-fix` pair).
  `nic_tuning` is the single owner of NIC offload state now. **Audit before
  deploying**: run `lspci | grep -iE 'I219|I218|I217'` on every bare-metal host
  and make sure each match is covered by `nic_tuning_overrides`. A host that is
  not covered keeps its current runtime offload state until the next reboot or
  link event and then silently loses the workaround — which is the failure mode
  the workaround exists for.
- **`base_fail2ban_ignoreip` defaults to loopback only.** The in-tree default
  trusted the LAN and the tailnet. Re-add those CIDRs or an admin source can be
  banned out of its own hosts.
- **Unattended-upgrades config is written unconditionally** on VMs and
  containers, rather than only when `/etc/apt/apt.conf.d/20auto-upgrades`
  already exists. A fresh image (or a later `apt install unattended-upgrades`)
  previously came up with automatic updates ON. APT ignores the file when the
  package is absent, so the only effect is a new file on hosts that lacked one.

### docker_engine

| Old | New |
|---|---|
| `docker_ce_version` (inv) | `docker_engine_ce_version` |
| `containerd_version` (inv) | `docker_engine_containerd_version` |
| `docker_buildx_plugin_version` (inv) | `docker_engine_buildx_plugin_version` |
| `docker_compose_plugin_version` (inv) | `docker_engine_compose_plugin_version` |

No alias shims: with the old names only, the role's entry assert fails the play
with a named message. That is deliberate — a stale version default silently
**downgrades** an engine, so a loud failure is the safer default. Everything that
reads the old names on the consumer side (version-check registry entries, the
version-pin gates, the `nextcloud`/`immich` deploy paths) needs the same rename.

### gitlab

New role. It was not previously in the collection, so "migration" means moving
`ansible/roles/gitlab` out of the consumer tree, switching the playbook to
`weisssrv.infra.gitlab`, and supplying the site values that were role defaults.

| Old | New | Note |
|---|---|---|
| `skip_gitlab_install` | `gitlab_skip_install` | molecule / `-e` only |
| `ssh_service_name` (shared) | `gitlab_ssh_service_name` | role-owned now; default `ssh` |
| `vm_additional_disks` | `gitlab_additional_disks` | **aliased** — no inventory change |

**Every optional feature now defaults OFF, and the endpoints default empty.**
Registry, Pages, SMTP, SAML and the sshd `AllowUsers` drop-in must be switched
on explicitly; `gitlab_external_url`, the NFS backup landing, the cert paths and
the CIDR lists are all empty by default. Each enabled block asserts its own
inputs, so nothing degrades quietly — but nothing works until it is set.

Behaviour that changes on first converge, in rough order of blast radius:

- **`gitlab.rb` renders differently** (Ruby-literal quoting via an `rb()` macro,
  `gitlab_timezone` in place of a hardcoded zone, omitted-when-empty lines), so
  the template reports changed once and `gitlab-ctl reconfigure` runs. That is a
  real production event — schedule it alone.
- An empty `gitlab_saml_required_groups` now **requires**
  `gitlab_saml_allow_all_users: true` rather than silently auto-provisioning
  every IdP user.
- `gitlab_backup_path` must equal `gitlab_backup_mountpoint` when the backup is
  NFS-backed (both the wrapper and the unit test that exact path for
  mountedness); asserted.
- The Web IDE Application-Settings pass is gated on a non-empty
  `gitlab_web_ide_extension_host_domain` (it previously ran on every deploy).
- New metrics file `gitlab_backup_secrets.prom`
  (`gitlab_backup_secrets_present`, `gitlab_backup_secrets_size_bytes`) — a
  tarball without `gitlab-secrets.json` restores to unreadable encrypted
  columns, and that was previously unalertable. The secrets copy no longer
  preserves timestamps, so its mtime is a freshness signal.
- The backup wrapper sources `compose_app`'s shared metrics library instead of
  defining its own. **Metric names are unchanged**, but a consumer's
  `deploy-gitlab` `changes:` list must now cover the `compose_app` role path too,
  or a library change stops redeploying gitlab.

New optional inputs (both default `""`, which omits the `gitlab.rb` line and
leaves the Omnibus defaults — the exporter on, bound to `localhost:9187`):

| Variable | Meaning | Default |
|---|---|---|
| `gitlab_postgres_exporter_enabled` | `postgres_exporter['enable']`; set `true`/`false` only to override Omnibus | `""` (line omitted) |
| `gitlab_postgres_exporter_listen_address` | `postgres_exporter['listen_address']`; set e.g. `0.0.0.0:9187` to scrape from off-host | `""` (line omitted) |

Publishing it exposes **unauthenticated** database metrics: scope the port at
the firewall.

### home_assistant

New role. Consumer API is unchanged — every `home_assistant_*` input keeps its
name. Three values that were role defaults are now required and asserted:
`home_assistant_host`, `home_assistant_trusted_proxies`,
`home_assistant_oidc_configure_url` (the OIDC discovery URL — the issuer host is
the EXTERNAL one).

New optional inputs: `home_assistant_ssh_user`, `_ssh_connect_timeout`,
`_ssl_certificate`, `_ssl_key`, `_oidc_scope`, `_oidc_username_field`,
`_oidc_block_login`, `_extra_config`.

**The deploy is now idempotent.** The role checksums the deployed
`configuration.yaml` + `secrets.yaml` over one ssh round trip; identical means
the stage, backup, install, `ha core check` and cleanup are all skipped. A
converged run reports `changed=0`. The **first** run after adoption still
deploys — the rendered header text differs — so expect one `.bak` cycle and one
config check.

The idempotency check assumes `sha256sum` exists in the HAOS SSH add-on shell
(busybox provides it). If it is ever missing, the run fails before anything is
staged, which is a safe failure.

### immich

New role. It replaces an in-tree role of the same name.

| Old | New | Note |
|---|---|---|
| `immich_ml_image` | `immich_machine_learning_image` | the in-guest CPU ML image; the old name collided with the `immich_ml` role's prefix. Not set in inventory → no action |
| `immich_internal_url` | *(removed)* | dead variable, referenced nowhere |
| `vm_additional_disks` | `immich_additional_disks` | **aliased** |
| `timezone` | `immich_timezone` | **aliased** |
| handler `Reload systemd` | `Reload systemd for immich-backup` | internal; handler names are play-global and the old one collided with base/nas_storage |

Seven inputs are now asserted: `immich_version`, `immich_postgres_version`,
`immich_postgres_digest`, `immich_valkey_version`, `immich_valkey_digest`,
`immich_external_url`, `immich_oauth_issuer_url` — plus
`immich_backup_nfs_server`/`_export` when the NFS backup is enabled.

Values that must be supplied, with a note each:

- `immich_ml_urls` — the default is the in-guest CPU container **alone**. The
  site's list puts the GPU endpoint first and the CPU container second, and
  **the order is the failover contract**.
- `immich_nginx_self_signed_subj` / `_san` — generic placeholders now
  (`/CN={{ inventory_hostname }}`, no SAN). Set them to keep the current
  placeholder identity until acme_certs pushes the real wildcard.
- `immich_oauth_button_text` — default changed to `Sign in with SSO`. Cosmetic
  but user-visible.
- `immich_nginx_real_ip_from` — **do not hand-copy node IPs.** It now derives
  from `immich_nginx_real_ip_groups` (default `[k3s_servers, k3s_agents]`) via
  `map('extract', groups)` → `ansible_host`, so it tracks a node being added or
  renumbered. A group name that does not exist yields `[]` rather than an error.

New optional inputs — a `postgres-exporter` compose sidecar for database-level
metrics, off by default (nothing is added to the stack until it is switched on):

| Variable | Meaning | Default |
|---|---|---|
| `immich_postgres_exporter_enabled` | Add the sidecar | `false` |
| `immich_postgres_exporter_version` / `_digest` | Image pin; asserted (as a resolved tag/digest) when enabled | `""` / `""` |
| `immich_postgres_exporter_image` | Full reference, derived from the two above; override for another registry or build | `quay.io/prometheuscommunity/postgres-exporter:<version>` |
| `immich_postgres_exporter_port` | Host port | `9187` |

It reuses the stack's own `DB_USERNAME`/`DB_PASSWORD` from `.env` (no new
secret) and the endpoint is **unauthenticated**: scope the port at the firewall.

### immich_ml

New role.

| Old | New | Note |
|---|---|---|
| `skip_immich_ml_deploy` | `immich_ml_skip_install` | **alias kept** |
| `immich_version` | `immich_ml_version` | **alias kept**, so one pin drives both halves |
| `timezone` | `immich_ml_timezone` | **alias kept** |

New: `immich_ml_render_device`, `_card_device`, `_device_dir` (the passthrough
device node paths) and `_health_retries` / `_health_delay` (the `/ping` wait
budget) — all defaulting to the values that were hardcoded. `immich_ml_version`
is asserted. No inventory action beyond the docker_engine pin rename.

### k3s

| Old | New |
|---|---|
| `kube_vip_interface` | `k3s_kube_vip_interface` |
| `kube_vip_version` | `k3s_kube_vip_version` |
| `skip_k3s_gpu_install` | `k3s_skip_gpu_install` |

`k3s_api_vip`, `k3s_registry_host_pins`, `k3s_storage_host_pins` and
`k3s_etcd_snapshot_nfs_server` keep their names but are now empty by default —
see [Externalized defaults](#externalized-defaults-name-unchanged-value-now-empty).

New: `k3s_internal_domain` / `k3s_tls_sans` (the apiserver SAN list is now an
input rather than a hardcoded `k3s.<internal_domain>`), `k3s_additional_disks`
(aliases `vm_additional_disks`), `k3s_server_group`, `k3s_skip_install`, and the
four GPU pins `k3s_gpu_driver_version`, `k3s_gpu_container_toolkit_version`,
`k3s_gpu_cuda_keyring_version`, `k3s_gpu_cuda_keyring_sha256` (each aliases the
inventory-wide `nvidia_*` name of the same suffix).

**The role carries no version pins of its own any more.** `k3s_version` and
`k3s_kube_vip_version` had role defaults that had already drifted behind the
inventory's; both are now asserted instead, so a dropped group_var fails the
play rather than silently installing a stale k3s or kube-vip.
`k3s_kube_vip_resources` is new (defaults byte-equal to what is deployed today),
and the kube-vip manifest regains `priorityClassName: system-node-critical`.

New opt-in: `k3s_metrics_server_override_enabled` (default **false**, so nothing
changes until a site sets it). It is gated on a live probe — the role checks
that this k3s packages metrics-server as a `HelmChart` and **fails with the
alternative** if it does not, rather than writing an inert `HelmChartConfig`. So
enabling it is safe to try: it either works or fails loudly at deploy time.

### nas_storage

| Old | New |
|---|---|
| `media_mover_bwlimit` | `nas_storage_media_mover_bwlimit` |
| `media_mover_cpu_weight` | `nas_storage_media_mover_cpu_weight` |
| `media_mover_io_class` | `nas_storage_media_mover_io_class` |
| `media_mover_io_priority` | `nas_storage_media_mover_io_priority` |
| `media_mover_io_weight` | `nas_storage_media_mover_io_weight` |
| `media_mover_nice` | `nas_storage_media_mover_nice` |
| `nas_appdata_base` | `nas_storage_appdata_base` |
| `nas_appdata_dirs` | `nas_storage_appdata_dirs` |
| `nas_appdata_group` | `nas_storage_appdata_group` |
| `nas_appdata_mode` | `nas_storage_appdata_mode` |
| `nas_appdata_owner` | `nas_storage_appdata_owner` |
| `nas_backup_apps_base` | `nas_storage_backup_apps_base` |
| `nas_backup_artifact_apps` | `nas_storage_backup_artifact_apps` |
| `nas_backup_artifact_metrics_enabled` | `nas_storage_backup_artifact_metrics_enabled` |
| `zfs_arc_max_bytes` | `nas_storage_zfs_arc_max_bytes` (alias: `zfs_arc_max_bytes`) |
| `media_mover_enabled` (inv) | `nas_storage_media_mover_enabled` |
| `media_mover_src` (inv) | `nas_storage_media_mover_src` |
| `media_mover_dst` (inv) | `nas_storage_media_mover_dst` |
| `media_mover_schedule` (inv) | `nas_storage_media_mover_schedule` |
| `mergerfs_mounts` (inv) | `nas_storage_mergerfs_mounts` |
| `nfs_exports` (inv) | `nas_storage_exports` |
| `samba_shares` (inv) | `nas_storage_samba_shares` |
| `zfs_pools` (inv) | `nas_storage_zfs_pools` |
| `zfs_scrub_enabled` (inv) | `nas_storage_zfs_scrub_enabled` |
| `zfs_scrub_schedule` (inv) | `nas_storage_zfs_scrub_schedule` |
| `smartd_enabled` (inv) | `nas_storage_smartd_enabled` |
| `smartd_archive_disks` (inv) | `nas_storage_smartd_archive_disks` |
| `smartd_nvme_disks` (inv) | `nas_storage_smartd_nvme_disks` |
| `smartd_ssd_disks` (inv) | `nas_storage_smartd_ssd_disks` |
| `smartd_tank_disks` (inv) | `nas_storage_smartd_tank_disks` |
| `nas_encrypted_bind_sources` (inv) | `nas_storage_encrypted_bind_sources` |
| `nas_swap_clean_enabled` (inv) | `nas_storage_swap_clean_enabled` |
| `nas_swap_clean_schedule` (inv) | `nas_storage_swap_clean_schedule` |
| `nas_swap_clean_stop_guests` (inv) | `nas_storage_swap_clean_stop_guests` |

The archive backup is now **opt-in and site-supplied**: it was an in-role dataset
inventory, and is now `nas_storage_archive_backup_enabled` (default false) plus
the required `_pool` / `_sources` (and optional `_vzdump_target`). Leaving the
opt-in unset on a host that already runs the timer **removes** the units and the
script rather than orphaning them. weisssrv's literal values are under
[Externalized defaults](#externalized-defaults-name-unchanged-value-now-empty).

`samba_nas_password` is no longer a variable — the role reads the
`SAMBA_NAS_PASSWORD` environment variable, and warns (does not fail) when unset.

### nextcloud

New role. It replaces an in-tree role of the same name; every rename keeps an
alias shim, so the inventory needs no mechanical rename here.

| Old | New | Shim |
|---|---|---|
| `skip_nextcloud_deploy` | `nextcloud_skip_install` | yes |
| `vm_additional_disks` | `nextcloud_additional_disks` | yes |
| `redis_version` | `nextcloud_redis_version` | yes |
| `node_exporter_host_textfile_dir` (read in the template) | `nextcloud_backup_metrics_dir` | yes |
| `external_domain` / `internal_domain` | `nextcloud_external_domain` / `_internal_domain` | yes |

What does need supplying:

- **OIDC is opt-in now** (`nextcloud_oidc_enabled` defaults `false`, was
  `true`). Leaving it off is not an outage — the deployed Nextcloud keeps its
  config — but the SSO wiring stops being reconciled, so it drifts. Set it true
  and supply `nextcloud_oidc_discovery_uri`.
- **Outgoing SMTP is opt-in**: `nextcloud_smtp_host` defaults to `""` and the
  `occ` mail pass is skipped when empty (it was unconditional, against a relay
  hardcoded in the role).
- `nextcloud_nginx_real_ip_trusted_addresses` defaults to `[]`. Derive it from
  the k3s groups rather than pasting node IPs — the README carries the
  expression.
- `nextcloud_backup_nfs_server` / `_export` when the NFS backup is enabled.

New optional inputs — a `nextcloud-postgres-exporter` compose sidecar for
database-level metrics (the existing `nextcloud-exporter` is application-level),
off by default:

| Variable | Meaning | Default |
|---|---|---|
| `nextcloud_postgres_exporter_enabled` | Add the sidecar | `false` |
| `nextcloud_postgres_exporter_version` | Image pin; asserted (as a resolved tag) when enabled | `""` |
| `nextcloud_postgres_exporter_image` | Full reference, derived from the version; override for another registry or build | `quay.io/prometheuscommunity/postgres-exporter:<version>` |
| `nextcloud_postgres_exporter_port` | Host port | `9187` |

It reuses the stack's own DB user and `NEXTCLOUD_POSTGRES_PASSWORD` (no new
secret) and the endpoint is **unauthenticated**: scope the port at the firewall.

New fail-fast asserts: the four image pins non-empty; at least one of
`nextcloud_external_host`/`_internal_host`; the NFS pair; `nextcloud_mail_domain`
when SMTP is on; and `nextcloud_oidc_discovery_uri` alongside the other OIDC
inputs. The role fails closed, so a missing value is a failed play rather than a
partial converge — but land the `group_vars` change in the SAME MR that switches
the playbook to the FQCN.

### node_exporter_host

No renames. New: `node_exporter_host_proxmox` gates the Proxmox-only textfile
collectors — smartmontools, drivetemp, and all four collectors
(corosync/zpool/smartmon/vzdump). It defaults **false**, and the role previously
derived the same thing from `groups['proxmox']` membership, so **a Proxmox host
that does not set it silently gets the exporter and nothing else**. Set it in
the Proxmox group.

Also new: `node_exporter_host_healthcheck_interval` (5min) and the liveness gate
it drives — a timer that probes the exporter's own port and restarts the unit
when it stops answering, emitting a restart metric. `curl` joins the package
list because the probe needs it.

One behaviour change to expect on a wedged host: the corosync collector now
**fails** rather than publishing `cpu=0` when corosync is running but produced
no usable sample. The old normalisation reported the healthy value for exactly
the wedged-at-100% condition the collector exists to catch, and refreshed the
success sentinel while doing it. Now the textfile is left untouched and the
staleness alert fires.

### plex

New role. It replaces an in-tree role of the same name.

| Old | New | Where the consumer sets it |
|---|---|---|
| `media_group` | `plex_media_group` | `host_vars` |
| `media_gid` | `plex_media_gid` | `host_vars` |
| `skip_gpu_drivers` | `plex_skip_gpu_drivers` | molecule / test docs |
| `skip_plex_service` | `plex_skip_service` | molecule / test docs |

`plex_media_group` deliberately does **not** alias the bare `media_group`,
because `nas_storage_media_group` does not either — an alias on one side only
would let a bare `media_group` drift the two apart silently.

Also required: `plex_cert_domain` and `plex_pfx_passphrase` (the passphrase
assert is `no_log`), with `plex_claim` optional. New:
`plex_custom_cert_enabled` (default **true** = today's behaviour) gates the
whole certificate hook, so a consumer with no pushed certificate is not forced
to invent a passphrase; `plex_cert_dir` and `plex_port` replace the literals the
reload script used.

The render-group membership is now gated on `getent group render` instead of a
blanket `failed_when: false`, so a genuine failure (a missing plex user) fails
the play rather than being swallowed.

The bind-mount preflight is stricter than the in-tree role's: `plex_config_dir`,
`plex_transcode_dir` **and** `plex_media_dir` must each pass `mountpoint -q`, not
merely exist (a stale mountpoint directory sends the library to the guest's root
filesystem). A consumer whose media path is a plain directory by design, or a
test container with no bind mounts, sets `plex_skip_service: true` — the single
escape, which also skips the enable/start/readiness steps.

### postfix_null_client

| Old | New |
|---|---|
| `mail_aliases` | `postfix_null_client_aliases` |
| `postfix_config` | `postfix_null_client_config` |
| `smtp_relay_host` | `postfix_null_client_relay_host` |
| `smtp_relay_port` | `postfix_null_client_relay_port` |
| `postfix_sasl_user` (inv) | `postfix_null_client_sasl_user` |
| `postfix_sasl_password` (inv) | `postfix_null_client_sasl_password` |
| `root_email_alias` (inv) | `postfix_null_client_root_alias` |

New required input: `postfix_null_client_mail_domain` (appended to
`inventory_hostname` to form `myhostname`).

### prometheus_exporter / textfile_collector / apt_signed_repo / compose_app / encrypted_swap / nfs_tls / nic_tuning / vfio_passthrough / zfs_arc_cap

No renames — these roles were already prefixed or are new.
`compose_app_nginx_self_signed_san` keeps its name but is now empty by default —
see [Externalized defaults](#externalized-defaults-name-unchanged-value-now-empty).

Three additive inputs in this group are worth knowing:

- `apt_signed_repo_stage_dir` (`/run/apt-signed-repo`, root-only `0700`) — key
  material is staged there instead of `/tmp` and the whole directory is removed
  on cleanup, closing the verify→dearmor TOCTOU.
- `nic_tuning_verify_offloads` (default **true**) + `nic_tuning_feature_names` —
  after applying an override the role reads the feature back with `ethtool` and
  **fails the play** if it did not take. The apply itself no longer fails the
  play; the read-back is the single owner of the diagnosis, and it is the only
  thing that catches an exit-0 no-op.
- `zfs_arc_cap_max_bytes` now defaults to the alias
  `{{ zfs_arc_max_bytes | default('') }}` (it was `""`, which made the README's
  alias table false). No effect where the two roles are gated apart; a host that
  ran both would get the same value written to the same file twice.

### proxmox_backup

| Old | New |
|---|---|
| `pve_storage` | `proxmox_backup_storage` |
| `pve_vzdump_jobs` | `proxmox_backup_vzdump_jobs` |

### proxmox_firewall

| Old | New |
|---|---|
| `pve_firewall_aliases` | `proxmox_firewall_extra_aliases` (host-backed aliases now derive from a per-host `firewall_alias` / `firewall_alias_comment`; this list is for addresses that are not inventory hosts) |
| `pve_firewall_config_dir` | `proxmox_firewall_config_dir` |
| `pve_firewall_enabled` | `proxmox_firewall_enabled` |
| `pve_firewall_log_level_in` | `proxmox_firewall_log_level_in` |
| `pve_firewall_node_dir` | `proxmox_firewall_node_dir` |
| `pve_firewall_skip_pveum` | `proxmox_firewall_skip_pveum` |
| `pve_firewall_staging_dir` | `proxmox_firewall_staging_dir` |

Address data that used to be literal in the template is now input, and **empty by
default** — a missed value silently drops rules:
`proxmox_firewall_admin_lan_cidrs` (required, asserted),
`proxmox_firewall_admin_ts_cidrs`, `proxmox_firewall_smb_client_cidrs`,
`proxmox_firewall_wan_wireguard_vips`.

`proxmox_firewall_security_groups` replaces the seven literal per-application
`[group ...]` blocks and defaults to **`[]`** — it ships no example set. A
worked example lives in the role's own README; the site owns the list. **This is
blocking for the migration**: without it `cluster.fw` renders with no
application groups, and Proxmox refuses or ignores any guest `.fw` referencing
an undefined group. Land the groups in the same MR as the collection adoption,
and diff the rendered `/etc/pve/firewall/cluster.fw` against the live file
before merging — only comment lines and one new `sg-dns` rule
(`+dc/k3s_nodes -p tcp -dport 3000`, making the adguard-exporter scrape explicit
rather than relying on `admin_lan` being the whole /24) should differ.

`proxmox_firewall_immich_ml_clients` is **removed**. It existed only to feed the
shipped immich-ml example group; with the groups now site data, the consumer
keeps the concept under a name of its own and interpolates it into its own
group definition.

### proxmox_ha

| Old | New |
|---|---|
| `ha_resources` | `proxmox_ha_resources` |
| `ha_rules` | `proxmox_ha_rules` |
| `storage_replication_jobs` | `proxmox_ha_replication_jobs` |

### proxmox_lxc

| Old | New |
|---|---|
| `lxc_admin_user` | `proxmox_lxc_admin_user` |
| `lxc_bridge` | `proxmox_lxc_bridge` |
| `lxc_cores` | `proxmox_lxc_cores` |
| `lxc_disk_size` | `proxmox_lxc_disk_size` |
| `lxc_gateway` | `proxmox_lxc_gateway` (required on the create path; no default) |
| `lxc_keyctl` | `proxmox_lxc_keyctl` |
| `lxc_memory` | `proxmox_lxc_memory` |
| `lxc_nameserver` | `proxmox_lxc_nameserver` |
| `lxc_nesting` | `proxmox_lxc_nesting` |
| `lxc_onboot` | `proxmox_lxc_onboot` |
| `lxc_searchdomain` | `proxmox_lxc_searchdomain` |
| `lxc_ssh_public_keys` | `proxmox_lxc_ssh_public_keys` |
| `lxc_startup_delay` | `proxmox_lxc_startup_delay` |
| `lxc_startup_order` | `proxmox_lxc_startup_order` |
| `lxc_swap` | `proxmox_lxc_swap` |
| `lxc_template` | `proxmox_lxc_template` |
| `lxc_template_storage` | `proxmox_lxc_template_storage` |
| `lxc_unprivileged` | `proxmox_lxc_unprivileged` |
| `lxc_bind_mounts` (inv) | `proxmox_lxc_bind_mounts` |
| `lxc_storage` (inv) | `proxmox_lxc_storage` |
| `lxc_gpu_passthrough` (inv) | `proxmox_lxc_gpu_passthrough` |

New: `proxmox_lxc_internal_domain` (aliases `internal_domain`; feeds
`proxmox_lxc_searchdomain`), `proxmox_lxc_bootstrap_fallback_dns`, and the
`proxmox_lxc_idmap_*` quartet.

### proxmox_vm

| Old | New |
|---|---|
| `cloud_image_name` | `proxmox_vm_cloud_image_name` |
| `cloud_image_url` | `proxmox_vm_cloud_image_url` |
| `cloudinit_dns` | `proxmox_vm_cloudinit_dns` (alias: `dns_servers`) |
| `cloudinit_gateway` | `proxmox_vm_cloudinit_gateway` (required on the Linux create path; no default) |
| `cloudinit_user` | `proxmox_vm_cloudinit_user` (alias: `admin_user`) |
| `virtio_win_url` | `proxmox_vm_virtio_win_url` |
| `vm_agent_enabled` | `proxmox_vm_agent_enabled` |
| `vm_bridge` | `proxmox_vm_bridge` |
| `vm_cores` | `proxmox_vm_cores` |
| `vm_cpu_type` | `proxmox_vm_cpu_type` |
| `vm_disk_size` | `proxmox_vm_disk_size` |
| `vm_guest_type` | `proxmox_vm_guest_type` |
| `vm_hostpci` | `proxmox_vm_hostpci` |
| `vm_install_iso` | `proxmox_vm_install_iso` |
| `vm_iso_storage` | `proxmox_vm_iso_storage` |
| `vm_iso_storage_path` | `proxmox_vm_iso_storage_path` |
| `vm_memory` | `proxmox_vm_memory` |
| `vm_ostype` | `proxmox_vm_ostype` |
| `vm_virtio_iso` | `proxmox_vm_virtio_iso` |
| `vm_windows_machine` | `proxmox_vm_windows_machine` |
| `vm_windows_ostype` | `proxmox_vm_windows_ostype` |
| `vm_windows_vga` | `proxmox_vm_windows_vga` |
| `vm_balloon` (inv) | `proxmox_vm_balloon` |
| `virtio_win_version` (inv) | `proxmox_vm_virtio_win_version` |
| `virtio_win_checksum` (inv) | `proxmox_vm_virtio_win_checksum` |
| `vm_storage` (inv) | `proxmox_storage` (kept neutral — it is the role's inventory contract, not a role tunable) |

`vm_additional_disks` is **not** renamed: `proxmox_vm_additional_disks` aliases
it, exactly as `k3s_additional_disks` does, so one `host_vars` block still feeds
both zvol creation and zvol mounting. New: `proxmox_vm_cloud_image_checksum`,
`proxmox_vm_cloud_image_dir`, `proxmox_vm_cloudinit_prefix_len`.

### qol

| Old | New |
|---|---|
| `admin_user` | `qol_admin_user` (alias: `admin_user`) |
| `nvim_colorscheme` | `qol_nvim_colorscheme` |
| `nvim_plugins` | `qol_nvim_plugins` |
| `omz_commit` | `qol_omz_commit` |
| `omz_plugins` | `qol_omz_plugins` |
| `omz_theme` | `qol_omz_theme` |

### resolv_conf

No renames. New: `resolv_conf_internal_domain` (aliases `internal_domain`) drives
`resolv_conf_search_domains`; `resolv_conf_nameservers` is a required input;
`resolv_conf_unsafe_writes` covers the bind-mounted-file case.

### restic_offsite

| Old | New |
|---|---|
| `rclone_deb_sha256` | `restic_offsite_rclone_deb_sha256` |
| `rclone_version` | `restic_offsite_rclone_version` |
| `restic_version` | `restic_offsite_restic_version` |
| `restic_repo_password` (inv) | `restic_offsite_repo_password` |
| `b2_key_id` / `restic_key_id` (inv) | `restic_offsite_b2_key_id` |
| `b2_application_key` / `restic_application_key` (inv) | `restic_offsite_b2_application_key` |

`restic_offsite_cache_dir` keeps its name but is no longer a default: it is a
required input, asserted alongside `restic_offsite_repo`. `restic_offsite_repo`,
`_sources`, `_zvol_sources` and `_excludes` also keep their names and are now
empty — the weisssrv values are under
[Externalized defaults](#externalized-defaults-name-unchanged-value-now-empty).

New, all with defaults: `restic_offsite_retry_lock` (`15m`; empty disables),
`restic_offsite_stale_lock_min_age_h` (6), `restic_offsite_verify_groups` (12).
`restic_offsite_keep_daily` moves 3 → 7 (a `--keep-last` floor counts
*snapshots*, so multiple runs in a day collapsed it onto few calendar days).

**The metrics split, and it needs an alerting change in the same window.**
`restic_offsite_last_run_success` / `_last_success_timestamp_seconds` are kept
and now mean "the whole run completed without error". Four gauges are new:

| Metric | Meaning |
|---|---|
| `restic_offsite_last_backup_success` / `_last_backup_timestamp_seconds` | flushed immediately after `restic backup` returns 0, so the upload fact survives whatever retention does next |
| `restic_offsite_last_prune_success` | the prune stage alone |
| `restic_offsite_retention_blocked` | 1 when the retention ceiling refused to prune |
| `restic_offsite_retention_pending_removals` | how many snapshots that refusal is holding |

Retention-ceiling overflow is now **non-fatal**: the run exits 0 and records
blocked/pending instead of failing. That is the point — a ceiling refusal is a
guard working, not a backup failing — but it means the wedge is invisible unless
something alerts on `restic_offsite_retention_blocked == 1`. Point the existing
failure/staleness alerts at `_last_backup_success` /
`_last_backup_timestamp_seconds` and add the retention alert **before** adopting,
or a stuck retention runs silent.

Two more operator notes: `restic-offsitectl unlock` is a new subcommand that
reaps a stale lock left by this host (a dead PID, older than
`_stale_lock_min_age_h`), and the first run after adoption restarts the rotating
deep verify at group 1 because the persisted cursor does not exist yet.

### smtp_relay

| Old | New |
|---|---|
| `mail_aliases` | `smtp_relay_aliases` |
| `smtp_tls_cert_dir` | `smtp_relay_tls_cert_dir` |
| `smtp_relay_host` (inv) | `smtp_relay_upstream` (the smarthost `[host]:port` the relay forwards to) |
| `smtp_gmail_user` (inv) | `smtp_relay_upstream_user` |
| `smtp_gmail_password` (inv) | `smtp_relay_upstream_password` |
| `smtp_relay_user` (inv) | `smtp_relay_sasl_user` |
| `smtp_relay_password` (inv) | `smtp_relay_sasl_password` |
| `smtp_submission_config` (inv) | `smtp_relay_submission_config` |
| `smtp_submission_enabled` (inv) | `smtp_relay_submission_enabled` |

`smtp_relay_hostname` and `smtp_relay_origin` derive from
`smtp_relay_internal_domain` (alias: `internal_domain`); both stay empty when it
is unset, and the effective-config assert names them rather than rendering an
empty `relayhost`.

**`smtp_relay_config` keeps its name and changes meaning: it is now a merge
layer, not a replacement.** The role's own defaults moved to
`smtp_relay_default_config`, and what the tasks and templates read is
`smtp_relay_effective_config = smtp_relay_default_config | combine(smtp_relay_config)`
(read-only, from `vars/`). A site that restates every key today renders a
byte-identical `main.cf`, so adoption is a no-op — but from now on a default
added to the role actually reaches the relay, which it could not before. Trim
the site value to the real deltas (`myorigin`, `mydestination`, `mynetworks`,
`smtpd_relay_restrictions`, cert paths if they differ, `smtpd_sasl_local_domain`)
and delete the rest.

While trimming, note the security default: the role now ships loopback-only
`mynetworks` with `permit_mynetworks` dropped from `smtpd_relay_restrictions`. A
site that overrides both to trust a whole LAN on port 25 is re-opening that
deliberately; narrow it to the hosts that actually relay.

### tailscale

No renames. `tailscale_auth_key` is gone: the key is read from the
`TAILSCALE_AUTH_KEY` **environment variable** so it never reaches argv or a fact.
New: `tailscale_version` and `tailscale_gpg_fingerprint` are now role defaults
(pinned) rather than site values.

### unbound

No renames. The managed drop-in moved from `<site>.conf` to
`unbound_dropin_name` (default `managed.conf`); `unbound_legacy_dropins` lists
names removed on convergence, so a site that used a differently named drop-in
adds it there. New: `unbound_use_caps_for_id`, `unbound_interfaces`.

Two things to plan for:

- **Adopting this role is not a no-op on a live resolver.** The old drop-in is
  deleted and the new one written in the same run (removal first, so there is no
  window with both), and the handler restarts unbound. Leaving the old file
  behind would be the dangerous case — it sorts after `managed.conf` in
  unbound's include glob and would win every duplicated `server:` scalar. Do the
  resolvers **one at a time**, and keep `unbound_legacy_dropins` at its default
  until both have converged and the directory is confirmed clean.
- `unbound_access_control` no longer ships `::1 allow`. Nothing listened on
  `::1` behind a v4-only `interface:`, and unbound's built-in default already
  allows loopback, so resolution is unchanged. To actually serve IPv6 loopback,
  add `::1` to `unbound_interfaces` **and** put the ACL line back — one without
  the other is the dead config this removed.

### unbound_exporter / zfs_exporter

No renames. Each now carries its own `*_version` + `*_checksum` defaults instead
of reading a shared inventory pin.

### zfs_encryption

No renames. New: `zfs_encryption_internal_domain` (aliases `internal_domain`)
derives `zfs_encryption_connect_url`; set the URL directly to decouple.
`zfs_encryption_install_zfsutils` is now a declared default (`true`) rather than
an undeclared `| default(true)` lookup — same effective value.

**Do one check before cutting over.** The role has retired the migration sweep
that removed stale `zfs-mount.service.requires/zfs-load-key@*.service` symlinks
and ran an unconditional `daemon-reload` on every host, every run. Confirm it
has nothing left to do, on every Proxmox host:

```bash
ls -l /etc/systemd/system/zfs-mount.service.requires/ 2>/dev/null
```

Expect "No such file or directory" or an empty listing. A surviving
`zfs-load-key@*.service` symlink must be deleted by hand followed by
`systemctl daemon-reload` — `systemctl disable` will not remove it, and it fails
`zfs-mount.service` (`Before=local-fs.target`) at the next boot.

Also: `zfs-mount-encrypted.service` is now rendered **only** where
`zfs_encryption_pools` is non-empty, and is removed where the list is empty. On
hosts with no encrypted pools that unit file disappears on first converge;
nothing references it there. Keep `zfs_encryption_pools` and
`nas_storage_encrypted_bind_sources` consistent — a host declaring encrypted
bind sources with an empty pool list would have those binds fail rather than
hang, because the ordering anchor they require no longer exists.

### zvol_mount

No renames. New: `zvol_mount_device_id_prefix`.

## Required inputs (asserted at role entry)

A value with no safe generic default is asserted by name rather than failing
inside a template or shell command. These are the loud failures — everything else
falls back silently, which is why the tables above matter.

| Role | Asserted | Condition |
|---|---|---|
| `acme_certs` | `acme_certs_domain`, `acme_certs_email`, `acme_certs_ssh_private_key`, `acme_certs_ssh_public_key`, plus the dnsapi hook | always |
| `adguard_home` | `adguard_home_admin_password` | always (and again before the API pass) |
| `adguard_sync` | `adguard_sync_version`, `_origin`, `_replica`, `_admin_user`, `_admin_password` | when `adguard_sync_enabled` |
| `alloy_host` | `alloy_host_version`, `alloy_host_loki_url`; `_loki_user`/`_loki_password` | credentials only for an `https://` endpoint |
| `base` | a surviving SSH login path (`base_admin_user` + `base_ssh_authorized_keys`, or `base_ssh_permit_root_login`, or `base_ssh_password_authentication`) | when SSH config is not skipped |
| `adguard_home` | `adguard_home_tls_server_name` | when `adguard_home_tls_enabled` |
| `docker_engine` | `docker_engine_ce_version`, `_containerd_version`, `_buildx_plugin_version`, `_compose_plugin_version` | unless `docker_engine_skip_install` |
| `gitlab` | `gitlab_external_url`, `gitlab_version`, `gitlab_root_password` | always |
| `gitlab` | each enabled feature's own inputs (registry / pages / SMTP / SAML URLs and credentials) | per enabled block |
| `gitlab` | `gitlab_backup_path == gitlab_backup_mountpoint` | when `gitlab_backup_nfs_enabled` |
| `gitlab` | `gitlab_saml_allow_all_users: true` | when `gitlab_saml_required_groups` is empty |
| `home_assistant` | `home_assistant_host`, `_trusted_proxies`, `_oidc_configure_url`, OIDC credentials | always |
| `immich` | `immich_version`, `_postgres_version`, `_postgres_digest`, `_valkey_version`, `_valkey_digest`, `_external_url`, `_oauth_issuer_url` | always |
| `immich` | `immich_backup_nfs_server`, `_export` | when `immich_backup_nfs_enabled` |
| `immich_ml` | `immich_ml_version` | always (aliases `immich_version`) |
| `nextcloud` | the four image pins; one of `nextcloud_external_host` / `_internal_host` | always |
| `nextcloud` | `nextcloud_oidc_discovery_uri` + OIDC credentials | when `nextcloud_oidc_enabled` |
| `nextcloud` | `nextcloud_mail_domain` | when SMTP is on (`nextcloud_smtp_host` non-empty) |
| `nextcloud` | `nextcloud_backup_nfs_server`, `_export` | when `nextcloud_backup_nfs_enabled` |
| `plex` | `plex_pfx_passphrase` (`no_log`), `plex_cert_domain` | when `plex_custom_cert_enabled` |
| `k3s` | `k3s_version`, `k3s_api_vip`, `k3s_token` (servers) / `k3s_agent_token` (agents) | always |
| `k3s` | `k3s_gpu_driver_version`, `_container_toolkit_version`, `_cuda_keyring_version`, `_cuda_keyring_sha256` | when `k3s_gpu_node` and not `k3s_skip_gpu_install` |
| `nas_storage` | `nas_storage_archive_backup_pool`, `_sources` | when `nas_storage_archive_backup_enabled` |
| `nas_storage` | `nas_storage_media_mover_src`, `_dst` | when `nas_storage_media_mover_enabled` |
| `postfix_null_client` | `postfix_null_client_mail_domain`, `_relay_host`, `_sasl_user`, `_sasl_password` | always |
| `proxmox_firewall` | `proxmox_firewall_admin_lan_cidrs` | always (an empty set locks :22 and :8006 out on every node) |
| `proxmox_lxc` | `proxmox_lxc_gateway`, `proxmox_lxc_nameserver`, `SSH_PUBLIC_KEY` (env) | unless `proxmox_lxc_skip_create` |
| `proxmox_vm` | `proxmox_vm_cloudinit_gateway`, `proxmox_vm_cloudinit_dns`, `SSH_PUBLIC_KEY` (env) | Linux guests, unless `proxmox_vm_skip_create` |
| `proxmox_vm` | `proxmox_vm_install_iso` | Windows guests |
| `resolv_conf` | `resolv_conf_nameservers` | always |
| `restic_offsite` | `restic_offsite_repo`, `restic_offsite_cache_dir`, `restic_offsite_repo_password`, the rclone pin pair | when enabled |
| `smtp_relay` | `smtp_relay_config` identity (`relayhost`/`myhostname`/`myorigin`), `_upstream_user`, `_upstream_password`, `_sasl_user`, `_sasl_password` | always |
| `vfio_passthrough` | `vfio_passthrough_pci_ids` | when passthrough is enabled |
| `zfs_encryption` | `zfs_encryption_connect_url`, Connect token | when `zfs_encryption_pools` is non-empty |
| `zvol_mount` | `zvol_mount_disks` (shape: `name` / `mount_point` / `fstype` / `scsi_slot`; conventionally the same list as the guest's `proxmox_vm_additional_disks`) | always |

Added in v0.7.0. The escape hatch for each is in
[Newly asserted](#newly-asserted--loud-where-it-used-to-be-silent), except the
`nas_storage` and `proxmox_firewall` rows — those break a consumer that bumps
without changing anything else, so they are in
[Breaking](#breaking--act-in-the-same-mr-as-the-bump) instead:

| Role | Asserted | Condition |
|---|---|---|
| `adguard_home` | `adguard_home_dhcp_enabled` is false | always |
| `compose_app` | `compose_app_nginx_site_template` is a non-empty absolute path | when the nginx front end is configured |
| `encrypted_swap` | `encrypted_swap_source_device` exists as a block device | when `encrypted_swap_require_source_device` |
| `immich` | `immich_nginx_real_ip_from` resolves non-empty | unless `immich_nginx_trust_no_proxy` |
| `k3s` | every `k3s_server_group` member names the same `k3s_kube_vip_interface` | on every server |
| `nas_storage` | every export `bind_source` is under a ZFS mount root, a declared MergerFS target, or carries an explicit BOOLEAN `zfs:` | always |
| `nas_storage` | every MergerFS union has a branch INSIDE a ZFS mount root, or carries an explicit BOOLEAN `zfs:` | always |
| `nas_storage` | a declared `zfs:` on any export or union is a boolean, whichever branch classified it | when `zfs:` is present |
| `proxmox_firewall` | every `_dns_admin_ports` / `_metrics_scrape_ports` entry has a valued `port` and a non-empty `sources` LIST | always |
| `proxmox_vm` | the requested memory does not shrink a live guest | unless `proxmox_vm_memory_shrink_ok` |
| `proxmox_vm` | `proxmox_vm_disk_size` is a bare GiB count | Windows guests |
| `restic_offsite` | `restic_offsite_repo_password` non-empty (`no_log`) | when enabled |
| `restic_offsite` | `restic_offsite_b2_key_id`, `_b2_application_key` non-empty (`no_log`) | when `restic_offsite_rclone_remote_type == 'b2'` |
| `restic_offsite` | `restic_offsite_zvol_sources` repeats no `zvol` and no `name` | when enabled |

Values read from the environment rather than a variable, because they must not
reach argv or a fact: `SSH_PUBLIC_KEY` (proxmox_vm, proxmox_lxc),
`TAILSCALE_AUTH_KEY` (tailscale), `SAMBA_NAS_PASSWORD` (nas_storage — now
reachable as `nas_storage_samba_password` for a non-env secret backend).
