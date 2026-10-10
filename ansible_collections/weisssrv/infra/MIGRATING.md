# Migrating weisssrv.infra

Per-release upgrade notes for the collection, newest first. Every released tag
has a section, even when there is nothing to do.

Role variables are consumer-visible API, so every rename, removal and changed
default is listed here. Most of them are **silent**: each alias and each default
is `| default(...)`, so a name you miss does not raise
`AnsibleUndefinedVariable`, it quietly takes the role default. Land the
inventory change and the pin bump in the **same merge request**.

Adopting the collection for the first time, from un-prefixed in-tree roles? The
one-time rename map is
[MIGRATING-from-in-tree-roles.md](MIGRATING-from-in-tree-roles.md).

> **Lifecycle.** The collection ships no changelog file
> ([VERSIONING.md](../../../docs/VERSIONING.md) § No changelog file), so this is
> its only per-release migration record. At tag time the release MR retitles
> `# Unreleased (next release)` to the tag being cut and opens a fresh empty one
> above it; `tests/test_migrating_sections.py` fails a bump whose newest titled
> section is not `galaxy.yml`'s version, or a released tag with no section.
> Sections are kept in full, newest first, so a consumer jumping several
> releases works through each delta in order. Prune a section only when no
> supported consumer can still be on the release below it.

---

# Unreleased (next release)

Nothing yet.

# v0.19.0

A follow-up pass on the v0.18.x review findings. Two roles change what they do
to existing state — `proxmox_ha` corrects replication comments it used to
stamp backwards, and `nas_storage` reclaims an excluded child's snapshots —
and one flag, `nas_storage_archive_backup_exclude_destroy_ok`, keeps its name
and its `false` default while changing what `true` means. Read the first
section before bumping a site that set it.

## Breaking — act in the same MR as the bump

| Role | What to do |
|---|---|
| `nas_storage` | `nas_storage_archive_backup_exclude_destroy_ok` keeps its name and its `false` default, but both arms mean something new. `false` no longer REFUSES the run: it proceeds, logs one WARNING per excluded child whose archive-side copy still exists — naming the dataset, this variable, and that the space is never reclaimed — and publishes the count as `archive_backup_excluded_orphans` in the node_exporter textfile, so the orphan is alertable rather than a journal line only. **Alert on it:** a run-stopping FATAL used to be the signal, and a WARNING is not one. `true` no longer merely permits a destruction the recursive receive was expected to perform — it destroys each such copy with `zfs destroy -r`, and a failed destroy fails the run before any replication. The v0.18.0 row below describing `receive -F` as the destroyer is **superseded**: `zfs send -R -X` prunes the excluded subtree from the stream, so `receive -F` never touches the copy. A site that set this flag `true` only to get past the old refusal must decide which arm it wants before the bump. |
| `nas_storage` | An excluded descendant now keeps **no** source-side `archsync-*` snapshot: the snapshot `zfs snapshot -r` just created is destroyed on every excluded child, and retention keeps no window there. The first run after the bump therefore destroys the whole accrued backlog on each excluded dataset in one pass. That is reclamation, not data loss — those snapshots were never replicated — but it is a one-shot bulk destroy, so take it on a run you are watching. The archive-side copy's own 3+6 window is untouched; destroying it is what the opt-in above guards. |

## Changed defaults and behaviour

| Role | What changed |
|---|---|
| `proxmox_ha` | A replication job's identity is **(VMID, target)**, not its id. Proxmox reverses a job toward the node a guest migrated to, so per-id targets permute by design. For a job that already exists, `schedule` and `comment` are now reconciled from the `proxmox_ha_replication_jobs` entry whose `target_node` equals the job's LIVE target (read from `pvesh get /cluster/replication`), not from the entry carrying that job id. On the first run after the bump, each job whose id↔target pairing Proxmox has permuted gets one `pvesr update` correcting its comment (and its schedule, where schedules are staggered per target) to the live target; the run after that is clean. The reverse behaviour — the role stamping the inventory id's target onto a permuted job, so a `--comment` named a node the job does not replicate to — is gone. No input change: write each entry's `comment` about its own `target_node` and the role handles the permutation. |
| `proxmox_ha` | A permutation whose target SET still matches is **reported, not rewritten**. A new `ID PERMUTATION on <host> (id -> live target -> inventory target): ...` line appears in the play output beside the existing orphan and source-drift banners, and the new `proxmox_ha_permuted_jobs` fact (a list of `{id, live_target, inventory_target}`) carries the same triples. It is informational: reassigning ids to match live state would force a full ZFS resync per touched target, so do not "fix" the inventory to chase it. Anything that greps the play output for drift banners sees one new string. |
| `nas_storage` | The incremental base (`latest_common_snapshot`) no longer counts a destination dataset whose mapped source is excluded. A kept orphan stops receiving snapshots, so without this it would drag the all-datasets count down and, once the last shared snapshot aged out, force a full multi-TB re-send. Verification after the first run: the NEXT run must log `Incremental: ...`, not `No common archsync-* snapshots; full replicate`. |
| `proxmox_lxc` | The admin `authorized_keys` reconcile **merges** instead of rewriting the whole file: every line not byte-identical to a managed key is preserved, so a forced-command or `from=`-restricted entry seeded in a container outside Ansible survives a converge. The consequence to act on: rotating `proxmox_lxc_ssh_public_keys` no longer removes the superseded key, because a plain key outside the managed set is an unmanaged line like any other. Revoke with one run at `proxmox_lxc_ssh_authorized_keys_prune: true`, or set that permanently for the old whole-file behaviour. The merge preserves an entry that is still there; it does not restore one an earlier run already stripped. |
| `adguard_home` | The `/control/dns_config` reconcile now sends **and compares** the rate-limiter subnet lengths. Both default to AdGuard's own values, so nothing moves on a site that left them alone; a site that narrowed them in the UI while the role defaults stood sees one reconcile back to 24/56. The fields exist in the dns_config schema from the AdGuard Home 0.107.4x line — an older build rejects the POST and the role's own assert fails loudly rather than drifting. |
| `home_assistant` | Documentation and one assert message only, no behaviour change: `home_assistant_trusted_proxies` renders a block Home Assistant imports on **first boot only**. A storage-migrated instance reads `/config/.storage/http`, so narrowing the rendered list changes nothing there. The README says why the block is still rendered and that the effective list is an owner edit followed by a restart; writing `.storage` from Ansible stays out of scope. |

## New variables (defaults preserve today's behaviour)

| Role | Variable | Default | What it does |
|---|---|---|---|
| `proxmox_lxc` | `proxmox_lxc_ssh_authorized_keys_prune` | `false` | Makes the managed key set authoritative again: the merge is skipped and the file is reduced to the rendered keys. One run with it true is how a rotated-out key is revoked. |
| `adguard_home` | `adguard_home_ratelimit_subnet_len_ipv4`, `adguard_home_ratelimit_subnet_len_ipv6` | `24`, `56` | The prefix the rate limiter buckets clients by. At the default `24`, one noisy non-whitelisted client consumes the bucket for a whole homelab `/24`, which makes a per-host `adguard_home_ratelimit_whitelist` ineffective; `32` makes the limit per client. A deliberate posture change, not part of adopting the release. |

## Library surfaces outside the collection

**`ci/validate/flux-lint.yml` renders every Kustomization through `flux
envsubst --strict`.** GNU `envsubst` reads only `${NAME}`, while
kustomize-controller's Go envsubst also reads bash modifiers (`${conf%/*}`) as
variables, so a file that passed the gate could still fail post-build
in-cluster and stop that Kustomization reconciling. Two new inputs carry the
parser: `flux_version` (default `2.9.0`) and `flux_sha256`. They apply to the
substitute (root) arm only, so the tenant arm downloads nothing. A consumer
whose cluster runs another Flux release passes its own, so the gate's parser is
the one its kustomize-controller uses.

**The job holds that CLI equal to the versions ConfigMap's `flux_version`.**
`flux_version` was a fourth Flux pin no gate checked —
`scripts/check-flux-version-pin.py` reads only the `FLUX_VERSION[=:]` spelling
— so a cluster that bumped Flux in `all.yml` kept linting with the old parser
silently. The substitute arm now compares the installed CLI against the
exported `flux_version` after `export-versions` and fails on a mismatch, naming
both versions; a leading `v` on either side is tolerated. **Act at the bump:** a
consumer whose ConfigMap declares a `flux_version` other than `2.9.0` must pass
the matching `flux_version`/`flux_sha256` inputs or the job reds. A ConfigMap
declaring no `flux_version` prints that the pin is held equal to nothing and
proceeds.

**The strict render reaches a consumer's `task lint` only through that
consumer's own copy of the render loop.** The include covers CI; `flux:lint`
runs the loop each consumer carries in `taskfiles/flux.yml`, so adopting this
release does not add `flux envsubst --strict` locally — the local copy has to
gain it too. The program is now on the extraction queue in `docs/SCRIPTS.md`
with the flags it needs, so the third copy is recorded rather than rediscovered.

**One pin set per tool across the CI surface, asserted by
`tests/test_pin_parity.py`.** Changed defaults: `flux-lint`'s
`kustomize_version` 5.8.1 → 5.8.2 (sha with it), `ci/lint/shellcheck.yml`'s
`image` `koalaman/shellcheck-alpine:v0.10.0` → `v0.11.0`,
`ci/lint/ansible-lint.yml`'s `ansible_lint_version` 26.8.0 → 26.9.0 (moved
together with `docker/molecule-ci/requirements.txt`),
`ci/review/pr-agent.yml`'s `image` 0.45.0 → 0.47.0, and
`ci/validate/terraform.yml`'s `image` `hashicorp/terraform:1.15` →
`1.16.5@sha256:c7926fe…` so the gate parses the line the consumers plan with.
`ci/github/ci.example.yml`'s `KUSTOMIZE_*` and `SHELLCHECK_*` env move with
them. A consumer taking the shellcheck default lints under a newer shellcheck
and may see new findings; the library's own tree is clean on 0.11.0.

**`ci/lint/docs-link-check.yml`'s `job_name` default is `lint-docs-links`.**
Every consumer already overrides it, so nothing moves for them; the GitHub
example's job id and `name:` were renamed with it. The template path is
unchanged. No other `job_name` default moved — `comment-length`,
`runbook-anchors` and `manifest-gates` also read as nouns, but all three
consumers take those defaults, so renaming them is a coordinated fan-out
rather than a names-only change.

**All three dind definitions pass
`--default-network-opt=bridge=com.docker.network.driver.mtu=<dind_mtu>` beside
`--mtu`** (`ci/templates/docker-dind.yml`, `ci/build/docker-build.yml` and the
library's own molecule jobs). A user-defined network — the kind molecule
creates — ignores `--mtu`, which is how TLS frames black-hole on a 1420-MTU
overlay. No input change.

**`ci/test/python-tests.yml` caches `.bin/`** under the prefix
`python-tests-bin`, keyed on the new `tools_cache_key_files` input (default
`["scripts/ci-fetch-tools.py"]`, so a pin bump busts it; one or two entries,
GitLab's `cache:key:files` limit). It is a no-op without a runner cache
backend, and correctness does not rest on it — see the next entry.

**`scripts/ci-fetch-tools.py` stamps the installed version and the binary's
own sha256 beside each binary.** A destination that merely existed used to count
as present, so a cached, pre-seeded or truncated binary of any version was
trusted forever and a version bump in the table silently did not take effect in
a job whose `.bin` survived. Each install now writes `<name>.version` with the
effective version, the asset's sha256 and the sha256 of the installed binary,
and a tool counts as present only when that stamp matches the resolved pin AND
the binary re-hashes to the recorded digest — which is what lets a `cache:` on
the directory be safe, since a restored cache carries binary and stamp
together. The stamp lands after the binary, so an interrupted install reads as
absent. A stamp written by an earlier release has no binary digest in it, so the
first run after the bump re-fetches each tool once. `--force` is unchanged.
Re-vendor it with the cache change above.

**Three gates now refuse shapes they used to pass vacuously.**
`check-secretstore-scope.py` reports a `namespaceSelector` carrying a key
outside `matchLabels`/`matchExpressions` (a singular `matchLabel:`, an extra
sibling) as unmodelled instead of as a plain non-match, because both
recognised arms were empty and the matcher admitted every namespace.
`check-scrape-wiring.py` fails a matched Service with no `spec.selector`
(it widened to the monitor's own labels and certified a policy against no
workload), fails a port name two selected workloads declare at different
numbers, and groups documents by effective namespace so a policy in one
namespace no longer admits a scrape in another. `check-hpa-vpa-invariant.py`
goes the other way: its `maxAllowed`-above-limit arm no longer fires on a VPA
at `updateMode: Off`, whose recommendation the kubelet can never apply — a
finding that was unfixable except by an allowlist entry in every consumer.

**`check-netpol-except-parity.py` takes `--corpus FILE` (`-` for stdin)** so a
placeholder-shaped repo can be judged after substitution: the `${...}` CIDR
skip is off there and a leftover placeholder is an operator error. **Act at the
bump:** a consumer that passes no `--corpus` keeps the vacuous LAN-escape arm
and gets the verdict it got before the flag existed, so wire the rendered
stream into the render-gate driver — a `render-gates.conf` row for
`run-render-gates.sh`, or `--corpus "$RENDER_ALL"` where the render loop
already has one. Its directory walk also globs `*.json`, counting a walked JSON
file as a manifest only while every document in it carries `apiVersion` and
`kind` — the Grafana dashboards under the same tree are left alone, and a
hand-edited one that lost a comma does not red the fence gate, while a file
named on the command line stays the gate's subject whatever it holds. An egress
peer carrying none of `ipBlock` / `podSelector` / `namespaceSelector` is now
reported as the empty peer Kubernetes reads as every destination.

**`check-lib-pins.py` compares repository names, not substrings.** `project in
value` counted any Galaxy name/source or include path whose URL merely
contained the library's project path — a fork, a path-prefixed mirror — as
installing the library, so its unrelated pin passed the gate instead of being
reported unchecked. The normalised repo-name comparison beside it was already
the correct test and is now the only one; it still matches the same repository
on another host.

**Three more gate scripts are offered on `scripts/vendorable-paths.yml`.**
`check-guest-endpoint-parity.py` reconciles the two forks weisssrv and the
cluster template each carried, taking the wider behaviour of each: every
`group_vars` and `host_vars` file is read for exports, an export client is
matched by network containment rather than `/32` equality, the cluster-config
read goes through `gate_common`, and a cycle in the group tree is an operator
error. It imports `gate_common.py` **and** `inventory_tree.py` from its own
directory, so vendor all three or it exits 2 on import.
`flux-secret-consumers.py` (reads `kubectl get -o json` on stdin) and
`taskfile_tree.py` (an importable module for a consumer's Taskfile gates) ship
byte-identical to what both consumers already run. `docs/SCRIPTS.md` gains a
contract section for each, plus an extraction queue naming the scripts still
duplicated.

**`ci/github/ci.example.yml` gained a `comment-length` job and passes
`--namespace-from-tree` to the scrape gate.** The new job runs the vendored
`python3 scripts/check-comment-length.py .` — stdlib-only, no config, the
GitHub twin of `ci/lint/comment-length.yml`. The scrape flag reads the
namespace from the one Namespace manifest under the tree, because a
byte-identically vendored workflow cannot carry a tenant's value; it is
consulted only when a monitor declares `matchNames`, so a tenant whose
Namespace the cluster operator owns is unaffected, and zero or several
Namespace manifests under a tree that does declare `matchNames` is the
operator error.

**`tests/copier_render.py`'s `check_registered_copies` takes an optional
`ref`** and forwards `--ref`. Without it the comparison runs against whatever
the library checkout holds rather than the release the consumer pins, so a
passing gate said nothing about the ref the copies were taken at and a failing
one told the operator to re-vendor backwards. Every current caller keeps the
argv it had; pass your own `WEISSSRV_LIB_REF`.

**`docs/INCLUDE-CONTRACT.md`'s adoption ledger matches the consumer
pipelines.** The `kubectl-setup` and `comment-length` rows move to adopted, and
the legend drops the clauses that described local renders those consumers no
longer do.


# v0.18.1

## Fixed

| Role | What changed |
|---|---|
| `proxmox_ha` | The live replication index is read with `pvesh get /cluster/replication --output-format json`. `pvesr list` accepts no `--output-format` on any PVE release, so the v0.17.1 read (which tolerated a failing read) always parsed an EMPTY index, and v0.18.0's fail-closed read turned that into a failed play on every source node. Expect the first real reconcile of jobs that already exist: a job whose target, schedule and comment match the desired entry is left alone, one absent cluster-wide is created, one marked `enabled: false` is deleted, and target-set drift is corrected by delete + recreate only while the guest is local. Orphans and source drift stay reported, never touched. No consumer input change. |

# v0.18.0

A review pass over the whole library. Most of it is additive, but four roles
change a default under you and `proxmox_firewall` hands two security groups back
to site data. Work the sections in order: what fails the play first comes first.

## Breaking — act in the same MR as the bump

| Role | What to do |
|---|---|
| `proxmox_firewall` | `sg-host-egress` no longer builds in TCP 2222 or TCP 31100. A site running egress filtering re-declares them through `proxmox_firewall_host_egress_extra_ports` (cluster scope, so `group_vars/all`). `sg-smtp-relay` stays library-owned, but its Loki NodePort egress rule is gone — a relay guest that pushes logs re-declares it through `proxmox_firewall_smtp_relay_extra_egress_ports`. |
| `proxmox_firewall` | `proxmox_firewall_smtp_relay_sources` is a new input defaulting to `[]`, which renders NO inbound rule in `sg-smtp-relay`, so the relay's `:25`/`:587` close at the bump. The old hard-coded `core-cluster` scope was removed because that IPSet is inventory-derived and failed `pve-firewall compile` on a site that does not define it. Every site running the relay MUST name its client sets in the same MR as the bump: `proxmox_firewall_smtp_relay_sources: [core-cluster]` (cluster scope, so `group_vars/all`). |
| `proxmox_firewall` | `proxmox_firewall_nftables` is new and defaults to `false`. It selects the nftables `proxmox-firewall` implementation on a node by rendering `nftables: 1` into that node's `host.fw`, instead of the iptables `pve-firewall`. It is per node, so set it in `host_vars`. `false` omits the option, so a node switched by hand reverts on the next converge. Install the `proxmox-firewall` package first (the role installs none) and validate one node before the fleet. |
| `proxmox_ha` | The rules/resources/datacenter reconcile is now `run_once` inside the role, delegated to the first node that answers a reachability probe. Drop the `serial: 1` / `max_fail_percentage` / `_ha_config_applied` / localhost-verify scaffolding from the calling play and run a plain `hosts: <proxmox group>`. The role no longer sets `become` on its own probe, so the play must set `become: true`. |
| `vfio_passthrough` | The gate is reconciled both ways: `vfio_passthrough_enabled: false` now REMOVES the grub, modprobe and modules-load drop-ins and notifies the boot-artifact handlers. Compose the role unconditionally — drop any play-level `when: vfio_passthrough_enabled`, or a host turned off is never reconciled off. Composing it on a host that configured VFIO by hand removes `/etc/default/grub.d/vfio-iommu.cfg`, `/etc/modprobe.d/vfio.conf` and `/etc/modules-load.d/vfio-pci.conf` and rebuilds the boot artefacts. Audit those paths across the group first, and set `vfio_passthrough_manage_absent: false` on the exceptions. |
| `nextcloud` | `nextcloud_oidc_allow_local_remote_servers` now defaults to `false`. A site whose OIDC provider resolves to a private address (split-horizon DNS) MUST set it to `true` in the same MR as the bump, or OIDC discovery breaks on the next converge. The value is reconciled in both directions, so the guard is restored automatically. |
| `gitlab` | `gitlab_nginx_real_ip_trusted_addresses` must resolve non-empty; it used to be accepted empty and nginx attributed every request to the fronting proxy. Set it, or set `gitlab_nginx_trust_no_proxy: true` for an nginx reached directly. |
| `zfs_encryption` | `zfs_encryption_connect_vault` no longer defaults to a vault name. It is `""` and asserted non-empty on any host with `zfs_encryption_pools` and no `zfs_encryption_key_command`. Name the vault holding the pool passphrases in inventory. |
| `restic_offsite` | The repo password is rendered to `<config_dir>/repo-password` (0600) and passed as `RESTIC_PASSWORD_FILE`; `RESTIC_PASSWORD` is gone from the env file. Converge before the next nightly run. |
| `k3s` | `k3s_metrics_server_override_enabled`, `k3s_metrics_server_replicas` and `k3s_metrics_server_resources` are removed — see Removed below. |
| `nas_storage` | `nas_storage_archive_backup_on_success_units` now defaults to `[]` and `nas_storage_swap_clean_conflicting_units` to `[archive-backup.service, media-mover.service]`. Both used to name `restic-offsite.service`, a unit a different, independently opt-in role ships, so a site without `restic_offsite` logged "Unit restic-offsite.service not found" after every successful replication. A site handing off to restic re-declares `nas_storage_archive_backup_on_success_units: [restic-offsite.service]` and adds `restic-offsite.service` back to `nas_storage_swap_clean_conflicting_units`. That second list is a safety interlock, not tidiness: without it swap-clean shrinks the ARC, runs `swapoff` and gracefully stops the guests in `nas_storage_swap_clean_stop_guests` while an offsite upload is still reading clones of their zvols. |
| `nas_storage` | Adding a `nas_storage_archive_backup_exclude` entry for a child that is ALREADY replicated destroys its archive-side copy and its whole snapshot history on the next run, because the recursive receive uses `-F`. Save the archived copy first, then set `nas_storage_archive_backup_exclude_destroy_ok: true` in the same MR — without it the run refuses and records a failed run. |

```yaml
# A site handing off to restic re-declares the hand-off and both halves of
# the swap-clean interlock.
nas_storage_archive_backup_on_success_units: [restic-offsite.service]
nas_storage_swap_clean_conflicting_units:
  [archive-backup.service, media-mover.service, restic-offsite.service]
restic_offsite_conflicting_units: [swap-clean.service]

# Keeps the relay's :25/:587 on the narrow, inventory-derived scope.
proxmox_firewall_smtp_relay_sources: [core-cluster]

proxmox_firewall_smtp_relay_extra_egress_ports:
  - {port: 31100, proto: tcp, comment: Loki push NodePort}

proxmox_firewall_host_egress_extra_ports:
  - {port: 2222, proto: tcp, comment: forge SSH}
  - {port: 31100, proto: tcp, comment: Loki push NodePort}
```

## Removed

| Role | Variable | Replacement |
|---|---|---|
| `k3s` | `k3s_metrics_server_override_enabled`, `k3s_metrics_server_replicas`, `k3s_metrics_server_resources` | None. The feature wrote a `HelmChartConfig` for metrics-server, which k3s ships as a static manifest set, so it could never apply. Add `metrics-server` to `k3s_disable` and ship metrics-server as a cluster-managed release. The role removes a `metrics-server-config.yaml` an earlier version left in the server manifests dir. Setting any of the three is now a no-op. |

Internal fact renames in `proxmox_ha`, listed for anyone reading the role's
output: `proxmox_ha_source_drifted_job_ids` -> `proxmox_ha_stale_source_job_ids`,
`proxmox_ha_source_drift_jobs` -> `proxmox_ha_misplaced_guest_jobs`. Neither is
an inventory input.

## Changed defaults and behaviour

| Role | Change |
|---|---|
| `nfs_tls` | `nfs_tls_scrub_client_cert` now defaults to `false`. The role no longer deletes `nfs_tls_cert_path` / `_key_path` on a client-only host unless a site opts in. Set it `true` on hosts that relied on the scrub. |
| `immich` | `immich_metrics_bind` now defaults to `127.0.0.1` (was `0.0.0.0`). The three unauthenticated metrics ports are closed unless a site widens it. A site scraping Immich from off-host must set it explicitly. |
| `nextcloud` | The host nginx front end emits `Strict-Transport-Security`, gated on `nextcloud_nginx_hsts_enabled` (default `true`). Turn it off where TLS terminates upstream and that hop sets the header. |
| `postfix_null_client` / `smtp_relay` | `main.cf` renders the whole merged key map instead of a fixed key list, so a parameter a site adds through `postfix_null_client_config_extra` or `smtp_relay_config` now reaches the file. The default render is the same parameter set, reordered alphabetically. A site forking the template to work around the fixed list can drop the fork. |
| `postfix_null_client` | The `mynetworks` default now includes `[::1]/128`. `inet_interfaces = loopback-only` binds the IPv6 loopback too, so a client reaching `localhost:25` over `::1` was outside `mynetworks`. Override `postfix_null_client_config_extra.mynetworks` for IPv4-only. |
| `smtp_relay` | `smtp_relay_default_config` gains `smtp_tls_mandatory_protocols`. A site overriding `smtp_relay_config` need do nothing. |
| `restic_offsite` | The three oneshot units now carry `TimeoutStartSec=`. `restic_offsite_timeout_start_sec` (default `6h`) is a shared floor the backup and restore-drill units take; each unit also has its own override, `restic_offsite_backup_timeout_start_sec`, `restic_offsite_verify_timeout_start_sec` and `restic_offsite_drill_timeout_start_sec`. The deep verify carries its own `12h` default and does NOT follow the shared floor, so size it with `restic_offsite_verify_timeout_start_sec` against the measured `restic check --read-data-subset` wall time (role README § Tunables). systemd leaves `Type=oneshot` with no start timeout, so before this release the backup, verify and restore-drill runs were unbounded. A run past the timeout is killed, recorded as a failed run and alerted on. |
| `k3s` | kube-vip authenticates with its ServiceAccount token (`--inCluster`) instead of the node's cluster-admin kubeconfig, so the shipped ClusterRole is now its real privilege. Set `k3s_kube_vip_host_kubeconfig: true` to restore the `/etc/rancher/k3s/k3s.yaml` hostPath mount. |
| `gitlab` | The role no longer saves the live iptables ruleset. The `netfilter-persistent save` handler and the `iptables-persistent` install are gone; the role installs `iptables` and re-applies its own REDIRECT rules at boot through `gitlab-ssh-redirect.service`. On a host converged by an earlier version, clear the frozen chains once: `iptables -F f2b-gitlab-ssh; systemctl restart fail2ban`, then remove the `f2b-` chains from `/etc/iptables/rules.v4`. |
| `gitlab` | The Web IDE Application Settings pass now runs under `gitlab_skip_install`. It is API-only and still gated on `gitlab_web_ide_extension_host_domain`, so a site leaving the domain empty is unaffected. A config-only converge with the domain set now also needs `gitlab_api_token`. |
| `base` | `base` no longer removes the legacy `atlantic-gro-fix` / `e1000e-tso-fix` oneshot units — NIC offloads are owned by `nic_tuning`. A host still carrying one needs a one-off removal: `ansible <host> -b -m file -a 'path=/etc/systemd/system/e1000e-tso-fix.service state=absent'`, and the matching `/usr/local/sbin/*.sh`. |
| `base` | `openipmi.service` is masked with an `/etc/systemd/system/openipmi.service -> /dev/null` symlink instead of the `systemd` module. No action; `systemctl unmask openipmi.service` still reverses it. |
| `base` | The unattended-upgrades stop/disable task no longer swallows failures. It is skipped when the unit is not installed, and a real systemd failure now fails the run. |
| `proxmox_lxc` | Every task delegated to the Proxmox node declares `become: true` itself, the same change as `proxmox_vm`, so a play driving Proxmox over `ansible_connection: local` can set `ansible_become: false`. |
| `nic_tuning` | Clearing `nic_tuning_overrides` now REMOVES the `/etc/network/interfaces.d/99-nic-<iface>-tuning.cfg` drop-ins the role wrote. A host that must keep one the role no longer declares should have it renamed out of that glob. |
| `nic_tuning` | Left `null`, `nic_tuning_bond_primary` now REMOVES both `bond-primary` and `bond-primary_reselect` lines from `/etc/network/interfaces` and clears `bonding/primary` live on every active-backup bond. A host whose preferred leg was pinned by hand outside Ansible must name that leg in `nic_tuning_bond_primary` in the same MR as the bump, or the pin is dropped and the bond parks on the leg it existed to avoid after the next failover. |
| `resolv_conf` | The resolver `options` line is now the list `resolv_conf_options` (default `[timeout:2, attempts:2]`, rendering byte-identically). Add `rotate` to spread host DNS load; an empty list omits the line. |
| `unbound` | Both `unbound.conf.d` writes run `/usr/sbin/unbound-checkconf` on the candidate file, so an invalid render fails the play instead of being restarted into a dead resolver. `unbound_skip_validate` (default `false`) is the escape hatch for a host without the binary. |
| `nas_storage` | The ARC cap is now included from `main.yml`, so it applies on a host that declares no `nas_storage_zfs_pools` and on one that sets `nas_storage_skip_zfs_operations`. |
| `nas_storage` | Turning `nas_storage_media_mover_enabled`, `nas_storage_swap_clean_enabled` or `nas_storage_backup_artifact_metrics_enabled` off now removes that component's units and script, the way the archive backup already did. A host that drops MergerFS loses `mergerfs-remount.service` the same way. Set `nas_storage_manage_absent: false` to leave every existing file alone. |
| `nas_storage` | The MergerFS remount cycle detects binds by mount device id and FAILS the play when a mount the inventory does not declare still holds a union, instead of force-unmounting it. The NFS exports it unexported are restored either way. |
| `proxmox_backup` | The vzdump hookscript no longer publishes `vzdump_backup_last_success_timestamp_seconds 0` for a failed run with no recorded history. The series is ABSENT until the first success, so a staleness rule needs an absence arm. The hookscript also emits `vzdump_backup_guests`. |
| `proxmox_firewall` | `proxmox_firewall_enabled: false` now also suppresses the guest `<vmid>.fw` writes and the `/etc/pve` directory creation. It still does not cover the `monitoring@pve` user and token, which stay on `proxmox_firewall_skip_pveum`. |
| `proxmox_firewall` | Emptying a guest's `guest_security_groups` now REMOVES `/etc/pve/firewall/<vmid>.fw`. Deleting the key entirely still leaves the file alone. |
| `proxmox_vm` | Every delegated task declares `become: true` instead of inheriting it from the play. A caller driving Proxmox over `ansible_connection: local` can now set `ansible_become: false` on the group, so no non-delegated task escalates on the operator's workstation or the CI runner. `tasks/guest-nic-firewall.yml` and `tasks/guest-startup.yml`, which `proxmox_lxc` shares, change the same way. |
| `proxmox_lxc` | The network-input assert (gateway, nameserver, searchdomain, netmask bits) fires when the container does NOT exist, instead of when `proxmox_lxc_skip_create` is false. A reconcile-only run no longer needs those values; a create run under `skip_create` now gets them checked. |
| `restic_offsite` | The retention gauges are ABSENT until a retention pass has run, instead of defaulting to `last_prune_success 1`. |
| `restic_offsite` | `restic` exit code 3 is now INCOMPLETE, not failed. The snapshot landed, so the run keeps it, publishes the new `restic_offsite_last_run_incomplete` gauge and exits 0. Retention is skipped on such a night, because an incomplete snapshot counts toward `--keep-last`/`--keep-daily` and would expire a complete one. A site alerting on `restic_offsite_last_run_success == 0` stops paging for it and should add a warning arm on the new gauge (role README § Metrics). |
| `restic_offsite` | `restic_offsite_zvol_sources[].mount_opts` defaults to `ro` instead of `ro,noload`. A clone is writable, so ext4 replays its journal and a crash-consistent snapshot walks without the `lstat … bad message` errors that produced rc=3. A site that set `mount_opts` explicitly is unaffected. |
| `alloy_host` | The journal `job` label is pinned by a relabel rule. `loki.source.journal` stamps its own component id over the `labels` block, so `{job="journal"}` selectors stopped matching after the Alloy 1.19.2 bump. Consumer dashboards and rules need no change. |
| `compose_app` | `write_prom_metrics` logs a `daemon.err` line (tag `<prefix>-metrics`) when the `.prom` write or rename fails. Still non-fatal and still returns 0; the failure used to be silent while the previous run's `_last_run_success 1` stayed published. |
| `nas_storage` / `acme_certs` / `gitlab` / `restic_offsite` | Every textfile-collector writer logs the same `daemon.err` line (tag `<unit>-metrics`) when its `.prom` write or rename fails. Still non-fatal, so a metrics failure never aborts a backup. |
| `acme_certs` | `cert_renewal_last_run_success` now covers the local certificate only: the renewal and the local reload. A failed distribution target (exit 2) leaves it at 1 and is reported by the new `cert_distribution_last_run_failed_targets` count in `cert_distribution_targets.prom`. A site whose only certificate alert reads `cert_renewal_last_run_success == 0` stops paging for a dead target and must add an arm on `cert_distribution_last_run_failed_targets > 0` (role README § Metrics). |
| `nas_storage` | Turning a component off now removes its `.prom` as well as its units and script. A frozen `*_last_success_seconds` left behind kept its staleness alert firing with no timer that could ever clear it. Set `nas_storage_manage_absent: false` to leave every existing file alone. |
| `encrypted_swap` | The self-skip arm (backing device absent) now removes `encrypted_swap.prom` along with the crypttab entry, the fstab line and the finalize unit, so a frozen `encrypted_swap_mapper_active 1` cannot keep reading healthy on a host that no longer has encrypted swap. Each plaintext-fallback branch also logs `daemon.err` under the tag `encrypted-swap`. |
| `apt_signed_repo` | `tasks_from: enable-components.yml` now FAILS when the deb822 sources file is missing, and when it carries no `Components:` line for the rewrite to land on (checked before the write, so a dry run — `--check` or an enclosing `check_mode: true` block — asserts the same precondition). Both were silent skips, and the caller then failed much later on a package living in a component nothing enabled. A host still on the one-line sources format needs `apt_repository` instead. |
| `adguard_home` | The role's API calls follow `adguard_home_web_bind` instead of always dialling `127.0.0.1`. A site that restricted the bind (as the role README recommends) no longer breaks the role's own reconcile. |
| `node_exporter_host` | The role's liveness probe and `node-exporter-healthcheck.timer` follow `node_exporter_host_bind_address` instead of always dialling loopback. A non-empty bind used to fail the play's own probe and then restart a healthy exporter every interval. |
| `nextcloud` | `nextcloud_nginx_hsts_enabled` is read through `| bool`, so the string spellings an inventory or `-e` supplies (`"false"`, `"no"`, `"0"`, `""`) turn the header off. Plain Jinja truthiness pinned a year-long `includeSubDomains` regardless. |
| `swap-clean` (`nas_storage`) | A pre-flight skip now emits `swap_clean_last_run_skipped 1` and `swap_clean_skip_reason_info{reason}`. A skip keeps `swap_clean_last_run_success 1` and advances the success timestamp, so a permanently-active conflicting unit was previously indistinguishable from a healthy nightly reset. Alert on the new gauge sustained over several days. |
| `swap-clean` (`nas_storage`) | The guest-stop escalation refuses a goal it cannot reach: unless stopping every running candidate could cover the target it stops nothing and records `swap_clean_last_run_skipped 1` with `swap_clean_skip_reason_info{reason="escalation unreachable"}` (`swap_clean_last_run_success` stays 0, as on any unsafe abort). The target is re-read as each guest stops, so a run whose stopped guests released their swap completes instead of stopping every candidate and aborting. No metric or variable is added or renamed. |
| `k3s` | `k3s-etcd-snapshot-copy.sh` logs a `daemon.err` line (tag `etcd-snapshot-copy-metrics`) when its `.prom` write, rename or directory creation fails, matching the other textfile writers. |
| `node_exporter_host` | The slabinfo collector publishes `node_slab_cache_present{cache}`, so a cache name the kernel renamed or merged away is distinguishable from a leak that stopped. `node_slabinfo_collector_last_success_seconds` is now emitted only on a successful run, matching its name; `node_slabinfo_collector_success` stays unconditional. |
| `node_exporter_host` | `zfs_pool_status_errors_total` is the MAXIMUM counter on any `zpool status` config row, not the sum over rows, so it is no longer additive. Zero-vs-non-zero rules are unaffected; a `delta()`/`increase()` detector or a magnitude panel must be re-expressed. |
| `node_exporter_host` | The vzdump hookscript reads its output directory from `/etc/default/vzdump-metrics-hook`, rendered from `node_exporter_host_textfile_dir`. It used to hard-code `/var/lib/node_exporter`, so a relocated textfile directory silently lost the series. |
| `restic_offsite` | The nightly `restic backup` output is streamed to the journal instead of being buffered to a temp file and replayed at the end. A multi-hour upload now shows progress, and a run killed at `TimeoutStartSec` keeps its log. |
| `restic_offsite` | `restic_offsite_timer_calendar` now defaults to `*-*-* 08:00:00`. The timer is the nightly trigger, and 08:00 keeps it clear of the `nas_storage` swap-clean window, which stops the guests whose zvols a run clones. A site that wires the `OnSuccess=` handoff from its archive job, or runs swap-clean on another schedule, sets its own value to keep tonight's time. |
| `immich` / `immich_ml` / `nextcloud` / `gitlab` / `home_assistant` / `plex` | Secret-presence asserts lost `no_log`. They test length only, so nothing is rendered; a missing credential now fails with the fail_msg naming the variable instead of "output has been hidden". Every task that renders a secret keeps `no_log`. |

### Behaviour changes worth a deploy window

- `k3s`: the rendered `/etc/rancher/k3s/config.yaml` changes, so the next k3s
  deploy bounces the control plane one node at a time.
- `proxmox_firewall`: every changed publish runs `pve-firewall compile` on the
  writing node and fails the play when it is refused, so the first run after the
  bump can fail loudly on pre-existing drift.
- `nas_storage`: the rendered `/etc/exports` header changes, so the first
  converge after the bump rewrites the file and fires `exportfs -ra`. The export
  lines themselves are unchanged. Run it where NFS clients can be restarted if a
  mount goes stale.
- `nas_storage`: the rendered
  `/etc/systemd/system/nfs-server.service.d/zfs-encrypted.conf` header changes,
  so the first converge after the bump RESTARTS `nfs-server`. Established NFS
  client mounts get stale file handles — delete the pods holding them to
  remount, and run it in a window where nfsd can be bounced.
- `tailscale`: route-advertising hosts gain
  `tailscale-bridge-masq-fix.{service,timer}`, which re-asserts the bridge-local
  masquerade ACCEPT rule every minute instead of only on a `tailscaled` restart.
- `immich_ml`: the deployed compose file loses its commented-out
  `MACHINE_LEARNING_MAX_BATCH_SIZE__OCR` example and the published port gains an
  explicit `0.0.0.0:` prefix, so the stack is recreated.
- `immich`: the metrics ports move from `0.0.0.0:` to `127.0.0.1:`, which
  recreates the two immich-server containers.
- `k3s`: the kube-vip DaemonSet manifest changes, so k3s re-applies it and the
  pod that owns the API VIP restarts on every server. Run it in a supervised
  control-plane window and follow the role README § Signing off a kube-vip change. The rollback is
  `k3s_kube_vip_host_kubeconfig: true` — an inventory edit, no role change, but
  it re-renders the DaemonSet and restarts the kube-vip pod on every server, so
  it needs the same supervised window.
- `nas_storage`: the first archive replication after an exclusion is added
  destroys the excluded child on the archive pool. Run it attended, with the
  archived copy saved if it still has value.

## Newly asserted — loud where it used to be silent

| Role | Assertion | When |
|---|---|---|
| `adguard_home` / `adguard_sync` | `ansible_architecture` maps to a release architecture (x86_64, aarch64, armv7l, armv6l, i386/i686, riscv64) | always |
| `gitlab` | `gitlab_nginx_real_ip_trusted_addresses` resolves non-empty (a mistyped inventory group resolves empty) | unless `gitlab_nginx_trust_no_proxy` is true |
| `immich` | `immich_server_image` and `immich_machine_learning_image` each end in a tag or digest, and never in `:@` | always |
| `node_exporter_host` | `node_exporter_host_slabinfo_collector` is only true where `node_exporter_host_proxmox` is, since the collector ships inside the Proxmox-host textfile collectors, and `node_exporter_host_slab_caches` is non-empty when the collector is enabled | always |
| `nic_tuning` | `/etc/network/interfaces` holds exactly one `bond-mode active-backup` stanza, and `nic_tuning_bond_primary` is one of its `bond-slaves` and a live slave of an active-backup bond where the host has bonding, checked before either `bond-primary` line is written | when `nic_tuning_bond_primary` is set |
| `nas_storage` | every `nas_storage_swap_clean_stop_guests` entry is `vmid:name:timeout-seconds` | when swap-clean is enabled |
| `nas_storage` | every `nas_storage_archive_backup_exclude` entry is a descendant of a declared source, and `zfs send -X` exists | when the exclude list is non-empty |
| `k3s` | a first server with no local etcd data has evidence a cluster does or does not exist: the API VIP or a peer answered, or `k3s_bootstrap_new_cluster` is set | on a multi-server group |
| `apt_signed_repo` | `apt_signed_repo_components` is non-empty, and the sources file carries a `Components:` line for the rewrite to land on | `tasks_from: enable-components.yml` |
| `nas_storage` | every MergerFS busy-mount probe produced an `rc`, so a probe that never ran refuses the remount cycle instead of reading as clean | when a MergerFS remount is needed |
| `vfio_passthrough` | `vfio_passthrough_cmdline_method` is a method the role implements | when passthrough is enabled |
| `postfix_null_client` | the host is Debian-family, at role entry rather than part-way through `postmap` | always |
| `proxmox_firewall` | cluster-scope `proxmox_firewall_*` inputs are scoped to the delegate, naming the variable that is set on the Proxmox group instead of `group_vars/all` | always |
| `proxmox_lxc` | `proxmox_lxc_disk_size` is a GiB value (`8` or `8G`) | on a create run |
| `zfs_exporter` | the host is x86_64, matching `unbound_exporter` | always |
| `zvol_mount` | a disk with no filesystem does not carry a partition table (`lsblk -d` FSTYPE + PTTYPE). `wipefs -a <device>` is the deliberate override. | always |
| `proxmox_firewall` | a guest naming `sg-smtp-relay` has a non-empty `proxmox_firewall_smtp_relay_sources`, instead of rendering a group with no `IN` rule and closing the relay | at cluster scope when any inventory guest carries the group, before `cluster.fw` is published, and again in the guest play |
| `restic_offsite` | both halves of the swap-clean interlock are declared: `nas_storage_swap_clean_conflicting_units` names `restic-offsite.service`, so swap-clean cannot shrink the ARC, `swapoff` and stop the guests whose zvol clones restic is reading, and `restic_offsite_conflicting_units` names `swap-clean.service`, so an offsite run already holding the lock is not undercut by a swap-clean night that starts after it | when `nas_storage_swap_clean_enabled` is true on the same host |
| `nas_storage` | `fuser` is installed before the MergerFS idle probe runs; a missing binary exits 3 rather than reading as an idle union. `psmisc` is installed alongside `mergerfs`. | when a MergerFS remount is needed |
| `proxmox_vm` / `proxmox_lxc` | the guest NIC firewall repair FAILS on a guest whose config has no `net0:`, instead of reporting ok. An adopted guest whose NIC is `net1` must be renumbered or excluded. | always |
| `nas_storage` | a failed `zfs set readonly=on` during archive lockdown demotes the run to `archive_backup_last_run_success 0` and logs the dataset, instead of being swallowed | on every archive replication |
| `proxmox_firewall` | a host in `proxmox_firewall_host_group` answers the reachability probe, or `proxmox_firewall_delegate_host` pins one; the cluster-scope writes no longer fall back to the group's first member | always |
| `proxmox_firewall` | every per-application security group name starts with a letter and runs 2 to 18 characters, `pve-firewall`'s own grammar (a leading digit previously passed the role and failed `pve-firewall compile`) | always |

## New variables (defaults preserve today's behaviour)

| Role | Variable | Default | What it unlocks |
|---|---|---|---|
| `nas_storage` | `nas_storage_manage_absent` | `true` | Lets a disabled component's units, scripts and `.prom` be converged away. Set `false` on a host adopted with hand-rolled units of the same conventional names. |
| `nas_storage` | `nas_storage_managed_marker` | `Ansible managed` | Substring identifying a role-written file — the literal every template's `# {{ ansible_managed }}` header renders. A file without it is reported and kept, never stopped or removed. Asserted non-empty on the de-provisioning path; set it when a site's `ansible_managed` no longer contains this string. |
| `nas_storage` | `nas_storage_pve_cluster_backup_lib_path` | `/usr/local/lib/nas-storage-backup-lib.sh` | Where the shared `write_prom_metrics` helper lands for the `/etc/pve` wrapper, which now sources it instead of carrying its own copy. Same series and values; the logger tag becomes `pve_cluster_backup-metrics`. |
| `proxmox_firewall` | `proxmox_firewall_compile_error_patterns`, `proxmox_firewall_compile_fail_on_line_errors` | `error`, `unable to`, `skip line`, `no such `, `invalid `, `unknown ` / `true` | What counts as a `pve-firewall compile` parse error when the command exits 0. The wider set catches PVE's own wording, such as `cluster.fw (line 12) : no such ipset 'x'`, which the two old substrings missed. |
| `acme_certs` | `acme_certs_ca_server` | `letsencrypt` | The ACME CA, replacing the hard-coded `--server letsencrypt`. |
| `adguard_home` | `adguard_home_stage_dir` | `/root/adguard-home-install` | Where the release tarball is downloaded and unpacked, instead of world-writable `/tmp`. On disk, not a tmpfs: the tarball and the extracted tree are ~55 MB together. Removed after the install. |
| `acme_certs` | `acme_certs_stage_dir` | `/run/acme-certs-install` | Where the acme.sh tarball is staged and extracted, instead of `/tmp`. Removed after the install. |
| `acme_certs` | `acme_certs_distribution_check_enabled`, `_schedule`, `_random_delay`, `_nice`, `_timeout`, `_retries`, `_retry_delay` | `true`, `*-*-* 05:40:00`, `30m`, `10`, `10m`, `2`, `10` | A new `homelab-cert-check.timer` runs `homelab-cert-reload.sh --check` daily: it probes every distribution target, writes the per-target and summary distribution gauges, and pushes nothing. `cert_renewal.prom` is untouched by the check. A failed probe is retried `_retries` times, `_retry_delay` seconds apart, before the target's gauge is written 0. The per-target gauge is now evaluated DAILY, not only at renewal, so a rule reading `cert_distribution_target_last_run_success == 0` needs a `for:` at least one check interval wide (the schedule plus its random delay), or a transient SSH failure pages until the next check. Set `_enabled: false` to remove both units. |
| `apt_signed_repo` | `apt_signed_repo_sources_path`, `apt_signed_repo_components` | `/etc/apt/sources.list.d/debian.sources`, `main contrib non-free non-free-firmware` | Inputs to the new `tasks_from: enable-components.yml`, which rewrites every `Components:` line in a deb822 sources file and then refreshes the apt cache (unless `apt_signed_repo_update_cache` is false). `k3s`'s GPU path now includes it instead of carrying its own copy. |
| `base` | `base_kernel_cmdline_args`, `base_skip_boot_update` | `[]`, `false` | Extra kernel boot parameters appended to `GRUB_CMDLINE_LINUX_DEFAULT` through `/etc/default/grub.d/99-base-kernel-cmdline.cfg`; one bare token per entry. Empty reconciles the drop-in away. Takes effect on the next reboot: the role runs `update-grub` (and `proxmox-boot-tool refresh`) and warns, never reboots. A host carrying `/etc/kernel/cmdline` is refused. `base_skip_boot_update: true` skips the GRUB regeneration. |
| `encrypted_swap` | `encrypted_swap_textfile_dir` | `node_exporter_host_textfile_dir`, else `/var/lib/node_exporter` | Where the finalize unit writes `encrypted_swap.prom`. Every failure branch exits 0 to keep the host swapped, so `encrypted_swap_mapper_active` is the only gauge that says a host fell back to plaintext swap; each fallback branch also logs `daemon.err` under the tag `encrypted-swap`, for a host with no textfile collector. |
| `gitlab` | `gitlab_nginx_trust_no_proxy` | `false` | Opting out of the real-IP assert, for an nginx reached directly with no proxy in front. |
| `gitlab` | `gitlab_ssh_redirect_chains` | `PREROUTING` plus `OUTPUT` on `lo` | The NAT chains carrying the managed Git SSH REDIRECT rule, which the role used to hard-code. |
| `gitlab` | `gitlab_bundled_prometheus_enabled`, `gitlab_bundled_alertmanager_enabled` | `""` | Empty omits the gitlab.rb lines and keeps Omnibus's own Prometheus and Alertmanager running. Set both false where the site already scrapes `/-/metrics`; `/var/opt/gitlab/prometheus` is left on disk to reclaim by hand. |
| `immich` | `immich_server_digest`, `immich_machine_learning_digest` | `""` | Appends `@sha256:…` to the two Immich images, so all four stack images can be digest-pinned the way postgres and valkey already are. |
| `immich` | `immich_server_mem_limit`, `immich_machine_learning_mem_limit` | `""`, `""` | Docker `mem_limit` on the two Immich containers. Empty is unlimited, today's behaviour; a server limit below the guest's RAM keeps a burst from taking the whole VM to the OOM killer. |
| `immich_ml` | `immich_ml_bind` | `0.0.0.0` | Publishes the AUTHLESS inference port on one address instead of every interface. The `/ping` health wait follows the bind. |
| `immich_ml` | `immich_ml_extra_env` | `{}` | Extra environment keys merged into the ML container, e.g. `MACHINE_LEARNING_MAX_BATCH_SIZE__OCR` (README § VRAM). |
| `k3s` | `k3s_gpu_apt_components` | `main contrib non-free non-free-firmware` | The component set the GPU path writes into the deb822 sources file. Pinned at the call site, so a site setting `apt_signed_repo_components` for another repo cannot drop the non-free components the NVIDIA packages live in. |
| `k3s` | `k3s_bootstrap_new_cluster` | `false` | Opt-in for a greenfield HA bootstrap. A first server with no etcd data now FAILS when neither the API VIP nor any peer in `k3s_server_group` answers, instead of rendering `cluster-init: true` against a quorum that may still be up. Single-server groups are unaffected. |
| `k3s` | `k3s_kube_vip_host_kubeconfig` | `false` | Restores the `/etc/rancher/k3s/k3s.yaml` hostPath mount for a kube-vip build that needs it. |
| `nas_storage` | `nas_storage_archive_backup_exclude`, `nas_storage_archive_backup_exclude_destroy_ok` | `[]`, `false` | Children left out of the recursive archive send, via `zfs send -X` (OpenZFS 2.3+), and the opt-in for destroying an excluded child that is already on the archive pool. See Breaking above before adding an entry. |
| `nas_storage` | `nas_storage_export_root` | `/export` | The NFS export root. |
| `nas_storage` | `nas_storage_archive_backup_on_success_units` | `[]` | Units started after a successful archive replication. Empty renders no `OnSuccess=` line. |
| `nas_storage` | `nas_storage_swap_clean_stop_timeout`, `nas_storage_swap_clean_conflicting_units` | `300`, `[archive-backup.service, media-mover.service]` | The swap-clean stop budget and the units it must not overlap. |
| `nas_storage` | `nas_storage_samba_workgroup`, `nas_storage_samba_server_role`, `nas_storage_samba_interfaces` | `WORKGROUP`, `standalone server`, `[]` | Samba identity that was literal in `smb.conf.j2`. |
| `nas_storage` | `nas_storage_smartd_disk_groups[].extra_flags` | unset | Appended to the smartd directive, e.g. `-n standby,q`. |
| `nextcloud` | `nextcloud_nginx_hsts_enabled`, `nextcloud_nginx_hsts_value` | `true`, `max-age=31536000; includeSubDomains` | HSTS from the terminating proxy; off where an upstream hop sets it. |
| `nic_tuning` | `nic_tuning_bond_primary_reselect` | `failure` | How the kernel reselects the preferred leg of an active-backup bond once it is named. |
| `nic_tuning` | `nic_tuning_bond_primary_manage_absent` | `true` | Lets an undeclared bond-leg pin be converged away: the `bond-primary` lines in `/etc/network/interfaces` and the live `bonding/primary`. Set `false` on a host whose preferred leg is pinned by hand outside Ansible. |
| `node_exporter_host` | `node_exporter_host_bind_address` | `""` | Empty keeps today's all-interfaces listener; set the scrape-facing address on a host with no firewall in front of the exporter port. The role's own liveness probe and the healthcheck timer follow it, and an IPv6 literal is bracketed. |
| `node_exporter_host` | `node_exporter_host_processes_collector` | `false` | Populates the node-exporter-full dashboard's System Processes rows for the host job. |
| `node_exporter_host` | `node_exporter_host_slabinfo_collector`, `node_exporter_host_slab_caches` | `false`, `[]` | An opt-in per-cache `/proc/slabinfo` collector for attributing a kernel slab leak. Adds scrape series per named cache. The cache list is per host and must be named when the collector is on — the role asserts it non-empty (and rejects a duplicate). |
| `plex` | `plex_gpu_driver_packages` | the Intel VA-API set | The driver packages installed for hardware transcode. Override for AMD (`mesa-va-drivers`) or NVIDIA, or set `[]` to install none. |
| `plex` | `plex_gpu_nonfree_repos` | `true` | Enables Debian's non-free components. Set false when the driver packages come from main or a vendor repo. |
| `postfix_null_client` | `postfix_null_client_config_extra` | `{}` | Parameters merged into the rendered `main.cf`. |
| `proxmox_firewall` | `proxmox_firewall_host_egress_extra_ports` | `[]` | Ports added to `sg-host-egress` — see Breaking above. |
| `proxmox_firewall` | `proxmox_firewall_smtp_relay_sources` | `[]` | The IPSets allowed at the relay's `:25` and `:587` in `sg-smtp-relay`; the scope was hard-coded to `core-cluster` before. Empty renders no rule, so the relay is unreachable until the site names its client sets — see Breaking above. |
| `proxmox_ha` | `proxmox_ha_delegate_host` | unset | Pins the node the reconcile is delegated to instead of taking the first that answers. |
| `proxmox_vm` | `proxmox_vm_additional_disk_backend` | `zfs` | Selects `tasks/disks-<backend>.yml`. The disk entry schema is unchanged. |
| `resolv_conf` | `resolv_conf_options` | `[timeout:2, attempts:2]` | The resolver `options` line as a list. |
| `restic_offsite` | `restic_offsite_conflicting_units` | `[]` | Units a run must not overlap; an active one makes the run a deliberate skip that retries on the next timer. Name `swap-clean.service` on a host that also runs `nas_storage` swap-clean. The role asserts it when `nas_storage_swap_clean_enabled` is true. |
| `restic_offsite` | `restic_offsite_rclone_deb_name` | derived from `restic_offsite_rclone_version` | rclone's amd64 artefact filename; override only for a differently-named artefact. |
| `tailscale` | `tailscale_require_authkey` | `false` | `true` fails the play when a node still has to join and `TAILSCALE_AUTH_KEY` is unset, instead of skipping the join silently. |
| `unbound` | `unbound_skip_validate` | `false` | Skips `unbound-checkconf` on a host without the binary. |
| `vfio_passthrough` | `vfio_passthrough_manage_absent` | `true` | Lets the disabled arm remove the three VFIO drop-ins. Set `false` to leave every drop-in alone. |
| `vfio_passthrough` | `vfio_passthrough_managed_marker` | first line of `ansible_managed` | Substring identifying a role-written drop-in. The disabled arm removes only files carrying it; a hand-written file is reported and kept. |
| `vfio_passthrough` | `vfio_passthrough_cmdline_method` | `grub` | The only method implemented. A host carrying `/etc/kernel/cmdline` is now refused instead of silently staging parameters the kernel never reads. |
| `unbound_exporter` | `unbound_exporter_listen_address` | `""` | Empty is all interfaces, today's behaviour; the endpoint is unauthenticated, so the host firewall is the only other control. |
| `zfs_exporter` | `zfs_exporter_listen_address` | `""` | Empty is all interfaces, today's behaviour. |

`proxmox_vm` also shares `tasks/guest-nic-firewall.yml` and
`tasks/guest-startup.yml` with `proxmox_lxc` through `tasks_from:`; edit them in
`proxmox_vm`.

## Scheduled removals

These `gitlab` tasks exist only to undo states earlier versions of the role
produced, and are kept for hosts converged before v0.7.0. They are removed in
v1.0.0, together with `molecule/default/verify.yml`'s assertions for them:

- `Remove the legacy GitLab backup root cron job`
- `Remove obsolete gitlab-shell filter` (`/etc/fail2ban/filter.d/gitlab-shell.conf`)
- the `-m comment` drift path in `tasks/ssh-redirect.yml`

`Remove old GitLab keyring files` stays permanently: it guards the upstream
install-script layout, not only this role's own past.

## Terraform modules

Two modules gained guards that refuse a configuration `v0.17.1` accepted, so a
`terraform plan` that passed before can now fail at validate time.

| Module | What changed | Remedy |
|---|---|---|
| `authentik-sso` | A negate-only policy binding no longer counts as protection: `bound_application_slugs` now takes `b.enabled && !b.negate`, so an application whose only binding is a negate binding trips the unbound-application precondition. | Pair the negate binding with an allow binding, or set `allow_unbound = true` on that application. |
| `authentik-sso` | Every `proxy_providers` entry must appear in `embedded_outpost.proxy_provider_keys` or carry `detached = true`, and an `oauth2_client_secrets` or `group_secret_attributes` key naming no provider or group now fails plan. | Add the key to the outpost, mark the provider `detached`, or drop the stale secret key. |
| `unifi-network` | New `networks` validations: a duplicate `subnet` (compared as the normalised network address, so two gateway forms of one range collide), `dhcp.start` above `dhcp.stop`, and a `dhcp.leasetime` that is not a Go duration. | Config edit. |
| `unifi-network` | New `wlans[*].passphrase` rule (8-63 printable ASCII) and `port_forwards[*].wan_interface` enum. | Config edit; both fail an existing config that breaks them. |

## Library surfaces outside the collection

**`scripts/gate_common.py` is a new required vendored file.** Every
manifest-corpus gate on `scripts/vendorable-paths.yml` imports it from its own
directory, as does `check-live-cpu-limits.py`. Vendor it alongside whichever ones
you take and register it in your vendored manifest, or each exits 2 with a
message naming the missing file.

**`scripts/ci_yaml.py` is a new required vendored file.** Every CI-reading
gate on the offer list imports it from its own directory, so vendor it alongside
whichever ones you take and register it in your vendored manifest, or each exits
2 with a message naming the missing file. `scripts/vendorable-paths.yml`'s own
comments mark each pair. `check-helm-repo-parity.py` loads `check-versions.py`
from its own directory the same way, so those two are vendored as a pair.

**`check-vendored-copies.py` gained `--scan CONSUMER_DIR=LIB_PREFIX`.** It
reports a file under that tree whose offered library twin at
`LIB_PREFIX/<relpath>` no manifest entry registers, so a consumer can drop its
own hand-rolled unregistered-twin scan. A `--scan` naming a directory that does
not exist is an operator error, never a silent skip.

**`check-version-checksums.py` now loads `check-versions.py` from its own
directory** for the registry loader, so the two are vendored as a pair. It also
honours `$CHECK_VERSIONS_CONFIG`, refuses a plaintext `http://` `checksum_url`,
and exits 2 when the registry declares no checksum pin at all unless
`--allow-empty` is passed.

**The offer list roughly doubled this release, from 43 paths to 108, and lost
one entry (`lint/yamllint-strict.yml`).** `scripts/vendorable-paths.yml` is the
list, with a comment on every path whose twin must be vendored with it; the pairs
are called out above. A consumer that keeps its own roles tree declares what it
takes in `scripts/vendored-manifest.yml`.

**Gates that used to pass on an empty scan now exit 2.**
`check-molecule-matrix-coverage.sh` refuses a run where both enabled halves hold
no `molecule.yml` and both matrices are empty (declare a half off with
`ROLES_DIR=""` / `INTEGRATION_DIR=""` instead),
`check-cluster-invariants.py` refuses a missing cluster config
(`--allow-missing-cluster-config` opts out) and a config declaring no
`cluster_lan_cidr` (`--allow-missing-lan-cidr`),
`check-backup-artifact-apps.py` refuses a run that pairs nothing
(`--allow-empty`), `check-helm-repo-parity.py` refuses
a corpus that declares no helm repo (`--allow-empty`), and
`check-role-readme-literals.py` refuses a run with no `--site-domain`
(`--no-site-domains`). `check-secretstore-scope.py` exits 2 on a
`namespaceRegexes` entry that does not compile, and `validate-helm-values.py`
on a `--sources-dir` file that does not parse as YAML.

**`scripts/check-comment-length.py` and `ci/lint/comment-length.yml` are new.**
The gate fails a comment block over three content lines, or eight when the block
opens `CRITICAL:`, over the whole tree including extension-less config files
(`Dockerfile`, `.gitattributes`, `.editorconfig`, `.ansible-lint`). Adopt it
after a sweep: run it locally first, because a repo with a backlog reds every
pipeline from the first run. Stdlib-only unless a YAML `--config` is passed;
offered on `scripts/vendorable-paths.yml`.

**`ci/validate/flux-lint.yml`'s `cluster_dir` no longer carries a usable
default.** It defaults to the empty string, and in substitute mode the job FAILS
at run time when it is empty (a cluster name is site data). An include that
omitted the input now reds flux-lint instead of validating a tree it does not
own; pass the consumer's own cluster directory.

**`ci/validate/flux-lint.yml`'s simple arm now gates on skip count.**
`allowed_skips` (default `"0"`) fails the job when kubeconform validated more
resources than that against no schema, and also when the summary carries no
`Skipped:` field — the signature of an unreachable or rate-limited CRD catalog.
A repo rendering a kind the pinned `crd_catalog_ref` does not carry raises
`allowed_skips` per kind or adds a `-schema-location` for it; a run that used to
pass green on a catalog fetch failure now reds and needs a retry.

**`ci/maintenance/version-check.yml` replaces `schedule_allow_failure` and
`default_branch_allow_failure` with `soft_fail_exit_codes`** (array, default
`[1]`). `[1]` soft-fails "updates available" while a checker error (rc 2) reds
the job; `[1, 2]` restores the old blanket behaviour, and `false` on either old
input maps to `[]`.

**`check-hpa-vpa-invariant.py` allowlists need a reason.** `cpu_limit_allowlist`,
`vpa_cap_allowlist` and the new `memory_ratio_allowlist` in `--policy-config`
are mappings of `"namespace/Kind/name": reason` — one accepted shape, so a list
of `{target, reason}` mappings is refused along with a bare string entry. Empty
mappings still load. Operator errors exit 2 rather than 1.

**`check-lib-pins.py` fails a requirements.yml whose git collection matches
nothing.** It used to pass silently. The collection is matched on the repository
name as well as the full project path, and on `source:` as well as `name:`, so a
mirror or a fork on another host is gated too. A requirements.yml with no git
collection at all stays a no-op. Repoint `--project` if your requirements.yml
names the collection by a path this does not reach.

**`observability/dashboards/` is not a library asset.** Shared Grafana dashboard
JSON stays in the consumer repos. A repo that wants the shared rows held against
a sibling registers them as `forked:` entries in its own
`scripts/vendored-manifest.yml` with a reason and a `reconciled_sha256`.

**`check-netpol-except-parity.py` `load_config` returns a `Policy`.** It no
longer mutates module globals, and `classify` / `unfenced_reach` / `scan_paths` /
`check_paths` take an optional `policy=`. Only importers of the module are
affected; the CLI is unchanged.

**`taskfiles/` removed.** The two go-task include fragments (`lint.yml`,
`flux.yml`) and their README are gone. No consumer included them — each repo
hand-writes the equivalent task bodies in its own Taskfile — and the fragments
claimed to mirror `ci/lint/*.yml` with nothing holding them in step. A repo that
wants them back takes the bodies from the tag that last shipped them.
`scripts/check-taskfile.sh` still follows `includes:`, so a consumer's own
fragments stay gated.

**`lint/yamllint-strict.yml` removed.** Offered but applied nowhere and vendored
by no consumer, and it diverged from ansible-lint's bundled yamllint config on
both `truthy` and `document-start`. For stricter YAML rules, run ansible-lint or
vendor `lint/yamllint-relaxed.yml` and tighten it.

**`lint/ruff.toml`, `lint/yamllint-relaxed.yml`, `lint/gitleaks.toml` and
`lint/secret-detection-ruleset.toml` changed.** Comment-only in the first two;
`gitleaks.toml` additionally anchors the `op://` and `[your-…]` allowlist regexes
at the start of the token. Finding counts over all four repos are unchanged.
Re-vendor the byte-identical copies and reconcile the forks.

**`molecule-shared/` is offered for vendoring.** `prepare-common.yml` and
`tasks/{container-warmup,prepare-apt-disable,prepare-base}.yml` are on
`scripts/vendorable-paths.yml`, so a consumer that keeps its own copies can hold
them byte-identical. The apt-lock wait in `container-warmup.yml` is bounded and
no longer needs `fuser`, and a failed `dpkg --configure -a` now says so.

**`version-bump-bot` is pinned to one branch.**
`ci/maintenance/version-bump-bot.yml` gains `run_branch` (default `main`) and
`gate` (default `$VERSION_BUMP_BOT_TOKEN`), and both rules require the pipeline
to be on `run_branch`. `check_command` is the ref's own shell running beside a
write-capable PAT, so the ref restriction is the control. Two consequences: the
token variable must be **Protected** as well as Masked, and a consumer whose
default branch is not `main` passes `run_branch` — otherwise the job is silently
not created, with no failing job to notice.

**CI toolchain defaults moved.** `ci/validate/flux-lint.yml`: kubeconform
0.6.7 → 0.8.0, kustomize 5.4.3 → 5.8.1, helm 3.18.4 → 3.22.0, each with a new
sha256. `ci/lint/ansible-lint.yml`: ansible-lint 25.12.2 → 26.8.0, held equal to
`docker/molecule-ci/requirements.txt` by `tests/test_lint_version_parity.py`.

**`ci/deploy/deploy-base.yml` `ansible_version` moves 11.6.0 → 14.4.0**
(ansible-core 2.18 → 2.21), and the published `ansible-deploy` image moves with
it (`tests/test_ansible_deploy_image.py` holds the pair equal). A consumer
taking the default gets a two-major interpreter change on the job that runs its
production playbooks: pass your own `ansible_version` input to stay on the 2.18
line, or schedule the move in a deploy window. `meta/runtime.yml` keeps its
`>=2.18.0` floor, but CI exercises only 2.21.4.

**`check-versions.py` drops the `plex` and `gitlab` registry categories.** Both
are now expressed with the generic `apt_repo` fetcher, and `--category
plex|gitlab` fails argparse (exit 2). Rewrite the registry entries:

```python
# before
{"var_name": "plex_version", "category": "plex"}
# after
{"var_name": "plex_version", "category": "apt_repo",
 "apt_url": "https://repo.plex.tv/deb/dists/public/main/binary-amd64/Packages",
 "apt_package": "plexmediaserver"}
```

the same for `gitlab-ee` against its own `Packages` index, adding
`apt_exclude_regex` where a suite carries release-candidate versions. Update any
CI job or task passing `--category plex|gitlab`.

**`check-role-inputs.py` gains `--allow-empty`.** Both arms still exit 2 when
they find nothing to examine, but the messages now name the legitimate third
case: a consumer that composes no opt-in role, or whose roles default every
asserted input. That consumer runs `--allow-empty` (or `--skip`) instead of
being told the collection dropped a convention.

**`flux-child-kustomizations.py` exits 2 on a `dependsOn` cycle.** The ordering
still prints, for diagnosis, but it cannot satisfy the declared dependencies, so
the status says so instead of a warning on stderr with exit 0.

**`check-dashboards.py` reads `generatorOptions`.** The Grafana sidecar label
and the folder annotation may come from the kustomization's file-level
`generatorOptions` or from an entry's own `options`, with the entry winning per
key. A directory using the standard shared-label idiom passes instead of
failing every dashboard.
A consumer that overrides any of these passes its own value and its own sha.

**The CRD catalog is pinned.** `ci/validate/flux-lint.yml` gains
`crd_catalog_ref`, defaulting to a `datreeio/CRDs-catalog` commit sha instead of
`main`, so a catalog rewrite cannot change what the gate accepts. Point it at an
internal mirror ref to self-host, and pair it with `expected_skipped_file` so an
unreachable catalog reds the gate instead of degrading the run to core kinds.

**Two release watch items.** `scripts/version-bump-mr.py` now passes its token
through `GIT_ASKPASS` so it never reaches git's argv; `--remote-url` is the
escape hatch if the shim misbehaves. `scripts/version-check-ci.py` makes one
`GET /user` call per run to learn the bot's user id and only edits a note it
authored, so a token without `read_user` posts a new note each pipeline instead
of refreshing one.

**`validate-helm-values.py` release entries no longer require `repo_name` /
`repo_url`.** They are optional overrides; the chart repo resolves from the
manifest's own `sourceRef`. Existing release files keep working unchanged.

**`ci/deploy/deploy-base.yml` gains an `image` input**, defaulting to
`python:3.13-slim` — what both cluster pipelines already set as their
pipeline-level default, so nothing changes until you pass something else. Point
it at the published `ansible-deploy` image to skip the apt and pip installs, or
at a digest-bearing name to pin the supply chain.

**`ci/deploy/kubectl-setup.yml` now installs jq.** Everything that reads
`kubectl -o json` needs it, and the fragment is the one step every kubectl
consumer passes through. The step is skipped when the image already ships jq,
handles apt and apk, and fails loudly on an image with neither, so a consumer
can drop its own jq install on the next bump.

**`ci/deploy/cluster-verify-base.yml` is a new fragment**: the kubectl-only base
for in-cluster verification, with no Ansible, SSH key or `hosts.env`. A cluster
that hand-rolls a verify base on top of `.install-1password` can extend this
instead. It references `.install-1password` and `.kubectl-setup` by their
default names, so both fragments must be included too.

**The molecule test image moves to `ansible==14.4.0`** (ansible-core ~=2.21.4).
`meta/runtime.yml` keeps its `>=2.18.0` floor, so nothing in the collection's
contract changes. `ANSIBLE_ALLOW_BROKEN_CONDITIONALS` is gone from the shared
molecule provisioner env: it was a no-op on the old core and, on the new one,
would have downgraded a non-boolean `when:` in a ROLE task from an error to a
warning.

# v0.17.1

**`unifi-network` — WLAN `minrate_setting_preference` is now console-owned.**
Added to the `unifi_wlan` resource's `ignore_changes`, alongside the
`minimum_data_rate_*_kbps` values it already leaves unmanaged. The provider
defaults the field to `auto`, so a manually-raised 2.4 GHz min-rate (set from
the console) was reverted on every apply — it now persists. No consumer input
change; the WLAN inputs are unchanged.

# v0.17.0

**`nic_tuning` — new `nic_tuning_disable_ipv6` knob.** A list of interfaces on
which to fully disable IPv6 (removing the `fe80::` link-local), for an IPv4-only
segment where an interface's untagged link-local is an unfiltered L2 path the
VLAN firewall never sees. Writes a slash-separator `/etc/sysctl.d/` drop-in and
applies live (no reboot); strictly per-interface, never `net.ipv6.conf.all`.
Additive and backward-compatible: defaults to `[]`, so no consumer action is
required until a host opts in.

# v0.16.0

**`unifi-network` — port forwards can enable per-forward WAN hit logging.** The
`port_forwards` map object gains an optional `logging` field (default `false`)
that drives the UniFi gateway's per-forward "Log" toggle. Additive and
backward-compatible: an existing `port_forwards` entry keeps logging off until it
opts in, so no consumer action is required.

# v0.15.1

**`tailscale` — a subnet router on a bridging host now reaches guests hosted on
itself.** A route-advertising node that is also a hypervisor (Proxmox, bridged
guests) could forward tailnet traffic to guests on other hosts but not to guests
on itself: `net.bridge.bridge-nf-call-iptables=1` made the packet re-traverse
`nat POSTROUTING` at the guest's fw-bridge, where Tailscale's own `0x40000` mark
re-masqueraded it to the router's tailnet IP, so the guest's reply returned to
the router and the originator never saw it. The role now installs a small script
(`/usr/local/sbin/tailscale-bridge-masq-fix`) plus a `tailscaled` `ExecStartPost`
drop-in that keeps one NAT `ACCEPT` rule above Tailscale's `ts-postrouting` jump.

- **No action required.** Subnet-router hosts (`tailscale_advertise_routes`
  non-empty) pick up the script and drop-in on the next deploy; the role's
  handler restarts `tailscaled` to apply it. It is NAT-table only — no
  filter/access change — and a no-op on a router with no bridged guests. Emptying
  `tailscale_advertise_routes` removes both.
- **Requires Tailscale in iptables (not nftables) netfilter mode** — it relies on
  the `ts-postrouting` chain and the `xt_physdev` match. No variables were renamed
  and no new inputs are required.

# v0.15.0

**`proxmox_vm` and `proxmox_lxc` now reconcile guest network/DNS config on
EXISTING guests, not only at creation.** Previously `ipconfig0`/`nameserver`
(VM) and `nameserver`/`searchdomain` (LXC) were written only on the create path
(`proxmox_vm_exists.rc != 0` / `pct create`), so a guest whose Proxmox-level net
config drifted from inventory after creation kept the stale value. Each role now
compares live `qm config`/`pct config` against inventory on every run and issues
a single idempotent `qm set`/`pct set` when they differ.

- **What converges.** A guest whose stored net config no longer matches
  inventory — the common case being a subnet renumber done in-guest while the
  Proxmox-level `ipconfig0`/`nameserver`/`searchdomain` stayed on the old
  addresses — is brought back in line on the next deploy.
- **It is safe / non-disruptive.** The VM reconcile only regenerates the
  cloud-init drive (`qm set --ipconfig0 --nameserver`), which cloud-init applies
  once per instance; the running guest's live network is untouched and nothing
  reboots. The LXC reconcile (`pct set --nameserver --searchdomain`) stages the
  change as pending, which Proxmox applies on the next container restart; a
  running container is not disrupted. Neither reconcile restarts a guest.
- **Action required only if you relied on out-of-band net config.** A consumer
  that deliberately set an existing guest's Proxmox-level `ipconfig0`/
  `nameserver`/`searchdomain` outside Ansible must now put the desired value in
  inventory (`proxmox_vm_target_ip` / `proxmox_vm_cloudinit_prefix_len` /
  `proxmox_vm_cloudinit_gateway` / `proxmox_vm_cloudinit_dns`; `dns_servers` /
  `internal_domain` for the LXC), or the next run reverts it to the inventory
  value. Both reconciles are guarded: the VM one is skipped for Windows guests,
  under `proxmox_vm_skip_create`, and when the cloud-init gateway/DNS inputs are
  empty; the LXC one is skipped when `proxmox_lxc_nameserver` is empty (a site
  that leaves resolution to the node DNS writes nothing). No variables were
  renamed and no new required inputs were added.

# v0.14.0

**No role or variable changed.** Both items are in the
`terraform/modules/unifi-network` module, and both change behaviour for a
consumer that adopts this release without editing anything.

- **`wlans[*].bands` is new, and its default hands the band set to the
  console.** The module used to write `wlan_bands = ["2g","5g"]` on every WLAN,
  so a 6 GHz band enabled in the UI was reverted by the next apply — which made
  the old README's "enable the band in the UI" advice impossible to follow.
  Unset (the default), the attribute is now not written at all: the controller
  owns the band set and a UI toggle sticks. **A consumer that relied on the
  module re-asserting 2.4 + 5 GHz must now say so explicitly**, `bands =
  ["2g","5g"]`, or its WLANs stop being held to those two bands. Nothing is
  destroyed either way — `wlan_bands` is Optional + Computed, so dropping the
  write leaves the live value alone; the change is in who wins the NEXT
  divergence. `6g` is accepted in an explicit list, but including it still
  fails WLAN creation on provider releases carrying upstream #406, so a 6 GHz
  SSID today is one that leaves `bands` unset.

- **`wlans[*].passphrase` is now validated as 8-63 PRINTABLE ASCII**, which is
  the actual WPA-PSK rule; the check was length-only before. An existing
  passphrase carrying a non-ASCII character — a smart quote or an accented
  letter picked up from a password manager — now fails `terraform plan` loudly
  instead of applying a key no client can use. The fix is to correct the
  1Password item, not the module.

# v0.13.2

**Nothing to migrate.** No role or variable changed; all three fixes are in the
`terraform/modules/unifi-network` module, absorbing controller behaviours that
UniFi Network 10.5 forces (details and the operator-facing consequences:
that module's README § Apply is supervised):

- Clients reserved on the `default`-keyed network are written WITHOUT a
  virtual-network override — the controller rejects the override for the
  default network, which failed those creates outright before.
- WLAN `ap_group_ids` is `ignore_changes`ed — the controller assigns the
  default AP group on every write and read it back, which made every apply
  flap and error.
- The site `ips` block is `ignore_changes`ed after creation — the controller
  keeps its own IPS mode regardless of the API write (and the failed write
  DISABLED a console-enabled IPS). **Day-2 IPS mode is console-owned from this
  release**; a consumer whose runbook told operators to manage it through
  Terraform should update that runbook.

# v0.13.1

**Nothing to migrate.** No role or variable changed; the fix is in the
`terraform/modules/unifi-network` module.

## `unifi-network` — networks always write `setting_preference = "manual"`

Every `unifi_network` the module writes is now pinned to `manual`. It used to
inherit the provider default `auto`, under which the controller treats the DHCP
DNS option, `domain_name` and `igmp_snooping` as its own and resets all three to
its defaults on every write — the module's own values are stripped from a
converged site, and the apply then fails with `Provider produced inconsistent
result after apply`. Confirmed on UniFi Network 10.5 with provider 0.55.0.

A consumer whose controller currently stores `auto` sees one in-place update per
network on the next plan, which is the fix landing. Nothing else changes: the
attributes the controller was stripping are the ones the module already
declares.

# v0.13.0

**Nothing is required.** Both changes are additive and the defaults reproduce
the previous render byte-for-byte — a consumer that only bumps the ref sees no
diff in `cluster.fw`.

## `proxmox_firewall` — client scopes for the two service surfaces

`templates/cluster.fw.j2` used to hard-code `+dc/admin_ts` / `+dc/admin_lan` as
the sources of the `sg-dns` `:53` rules and the `sg-k3s-ingress-int` `:80`/`:443`
rules. Those two surfaces are now parameterized:

| Variable | Default | Renders into |
|---|---|---|
| `proxmox_firewall_dns_client_sources` | `[admin_ts, admin_lan]` | `sg-dns` `:53` tcp+udp |
| `proxmox_firewall_k3s_ingress_int_sources` | `[admin_ts, admin_lan]` | `sg-k3s-ingress-int` `:443` then `:80` |

Each list entry renders one `+dc/<name>` rule per port, in list order, in the
same rule shape as before — so the defaults are literally the old four and four
lines. Leave them unset and nothing changes.

Set them when the site splits the **management** plane from the **client**
plane, which is what VLAN segmentation forces: `admin_lan` shrinks to the
management subnet (it gates `:22`, `:8006`, `:6443`), while resolution and the
internal ingress still have to answer the user VLANs. Declare those subnets as
their own IPSets with `firewall_ipset_special_entries` and point the two
variables at them:

```yaml
firewall_ipset_special_entries:
  dns_clients:
    - {ip: 10.0.20.0/24, comment: home VLAN}
    - {ip: 10.0.30.0/24, comment: iot VLAN}
  lan_clients:
    - {ip: 10.0.20.0/24, comment: home VLAN}

proxmox_firewall_dns_client_sources: [admin_ts, dns_clients]
proxmox_firewall_k3s_ingress_int_sources: [admin_ts, lan_clients]
```

The resolver's *admin* surfaces are deliberately not covered by this: they stay
on `proxmox_firewall_dns_admin_ports`, because `:3000` answers a reusable admin
credential in the clear and must not inherit the client scope.

## `terraform/modules/unifi-network` — new module

`terraform/modules/unifi-network` codifies a UniFi site's networks/VLANs,
firewall zones and zone-based policies, WLANs, client reservations, port
forwards and site settings. Adopting it is opt-in: a consumer that does not call
the module sees nothing new on the ref bump.

# v0.12.1

No migration steps. The flux-lint template's unknown-substitution-key check
now strips Flux's `$${` escape before scanning (its malformed-`${` sibling
already did), so a legitimately-escaped `$${var}` — e.g. a Grafana panel
variable in a dashboard ConfigMap — no longer false-positives.

# v0.12.0

No migration steps. Additive only: every consumer-included CI job template
declares a right-sized `KUBERNETES_CPU_REQUEST` via a new
`job_cpu_request` input (defaults per job class, no CPU limits — CPU is
compressible), completing the per-job resource declaration the v0.11.0
memory inputs began. The runner-side
`cpu_request_overwrite_max_allowed` ceiling ships in the consumer.

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
  delegations via a persisted `fs.leases-enable=0` drop-in (the role README
  documents the trade-off).
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

The one-time v0.6.0 adoption map (the un-prefixed -> prefixed rename a repo
works through once) is
[MIGRATING-from-in-tree-roles.md](MIGRATING-from-in-tree-roles.md). This section
is the delta for a consumer already on the collection: what this release breaks,
what it asserts, and what it adds.

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
| `ci/build/docker-build.yml` | NEW `login_registry` / `login_user` / `login_password`, defaulting to the `$CI_REGISTRY` trio the job used to hard-code. NEW `schedule_when` (`on_success` \| `never`, default `on_success`) makes the scheduled rebuild opt-out-able. Every build now applies OCI provenance labels (`org.opencontainers.image.{source,revision,version,title}`) with `--label`, which overrides a same-key `LABEL` in a consumer's Dockerfile. **`cpu_selector` no longer defaults (a node label is site data) and is now REQUIRED.** Every include must pass it in the same MR as the bump, or pipeline creation fails with "required input not provided". |
| `ci/security/secret-detection.yml` | `cpu_selector` no longer defaults (a node label is site data) and is now REQUIRED. Every include must pass it in the same MR as the bump, or pipeline creation fails with "required input not provided". |
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

