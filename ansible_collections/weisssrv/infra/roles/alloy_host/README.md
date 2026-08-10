# weisssrv.infra.alloy_host

Installs Grafana Alloy as a **host-side journald log shipper**: hypervisor
hosts, LXCs and VMs, plus the k3s nodes themselves, pushing to a Loki endpoint.

## Why a host-side Alloy

Container logs inside k3s are collected by the in-cluster Alloy DaemonSet. The
host-side Alloy covers what the cluster cannot see:

- hypervisor host journald
- LXC and VM systemd units
- k3s server/agent unit logs (kubelet/containerd/etcd themselves)

Both write to the same Loki backend; labels distinguish the source.

## Configuration

| Variable | Meaning | Required |
|---|---|---|
| `alloy_host_version` | apt package version to pin (`alloy=<version>`), then `dpkg`-held so an `apt upgrade` cannot move it | yes |
| `alloy_host_loki_url` | Loki push endpoint | yes |
| `alloy_host_loki_user` / `_password` | basic-auth credentials; asserted non-empty when the endpoint is `https://` (an authenticating proxy) | for https |
| `alloy_host_wal_enabled` | on-disk WAL for `loki.write` | no (`true`) |
| `alloy_host_wal_max_segment_age` | WAL segment cut interval — bounds replay lag and disk use | no (`1h`) |
| `alloy_host_journal_max_age` | how far back a restarted Alloy re-reads the journal | no (`3h`) |
| `alloy_host_http_port` | Alloy HTTP listen port, used by the post-deploy `/-/ready` probe | no (`12345`) |

Point `alloy_host_loki_url` at an authenticated ingress hostname; a plaintext
in-cluster endpoint (a NodePort, say) is a legitimate outage fallback and skips
the credential assert. Because Loki usually runs *inside* the cluster these
hosts carry, the WAL is what keeps an outage from silently dropping the very
journals that explain it.

## Relabeling contract

Two constraints in `templates/config.alloy.j2` are easy to break by tidying, and
both are asserted on the rendered config by the Molecule verify.

**Apply the rules exactly once.** `loki.source.journal` forwards straight to
`loki.write` and passes the rules by reference
(`relabel_rules = loki.relabel.journal.rules`); `loki.relabel "journal"` is a
rules holder with `forward_to = []`. Routing entries through that component as
well applies the rules a second time, after Alloy has dropped the `__journal_*`
metadata, so each `target_label` is set to `""` — i.e. deleted, and every
`unit=` dashboard query silently returns nothing.

**`unit` is the only journal stream label, deliberately.** Every rule here
becomes a Loki stream label, and each one multiplies the chunks the ingester
holds open for up to `max_chunk_age`. `priority` (a pure ~1.4x multiplier with
no dashboard consumer) and `hostname` (a duplicate of the `host` label the
source already sets) are not mapped. Adding one means re-checking
`max_global_streams_per_user` and the Loki ingester's memory limit.

**Sender/receiver pair.** `alloy_host_journal_max_age` is the sender half of a
pair with Loki's out-of-order accept window (`ingester.max_chunk_age / 2`).
Re-read entries older than that window are pushed and then rejected
`too_far_behind` and lost, in short restart-shaped bursts. Keep the two in
lockstep: raise both or neither.

## Files

- `tasks/main.yml` — adds the Grafana apt repo (fingerprint-verified, via
  `weisssrv.infra.apt_signed_repo`), installs + holds Alloy, manages
  `CUSTOM_ARGS` in `/etc/default/alloy` and the config file; relies on the
  packaged systemd unit
- `templates/config.alloy.j2` — Alloy config (journald → Loki)
