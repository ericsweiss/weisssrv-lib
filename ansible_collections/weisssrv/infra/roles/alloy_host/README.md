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

Point `alloy_host_loki_url` at an authenticated ingress hostname; a plaintext
in-cluster endpoint (a NodePort, say) is a legitimate outage fallback and skips
the credential assert. Because Loki usually runs *inside* the cluster these
hosts carry, the WAL is what keeps an outage from silently dropping the very
journals that explain it.

## Relabeling contract

`loki.source.journal` applies `loki.relabel.journal.rules` **inside the source**,
while the `__journal_*` metadata still exists, and forwards straight to
`loki.write`. The `loki.relabel "journal"` component therefore declares rules
only and keeps `forward_to = []`. Routing entries through it as well applies the
rules a second time after the metadata is gone, which sets `unit`, `priority`
and `hostname` to `""` — i.e. deletes them, and every `unit=` dashboard query
silently returns nothing.

**Deliberate change from the pre-collection role**, which did route the source
through `loki.relabel.journal` and so shipped every host journal stream without
those labels. Adopting this role restores them, which is a visible change to
live log labels: dashboards and alert rules that worked around the missing
labels need re-checking.

## Files

- `tasks/main.yml` — adds the Grafana apt repo (fingerprint-verified, via
  `weisssrv.infra.apt_signed_repo`), installs + holds Alloy, manages
  `CUSTOM_ARGS` in `/etc/default/alloy` and the config file; relies on the
  packaged systemd unit
- `templates/config.alloy.j2` — Alloy config (journald → Loki)
