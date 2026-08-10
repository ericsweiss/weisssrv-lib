# zfs_exporter

Installs the [pdf/zfs_exporter](https://github.com/pdf/zfs_exporter) on a ZFS
host to expose pool/dataset metrics (health, usage, scrub status,
fragmentation) to Prometheus.

Thin wrapper over `weisssrv.infra.prometheus_exporter` (tarball artifact): that
role downloads + checksum-verifies + installs the binary and enables/health-checks
the service; this role owns the pin and the systemd unit.

The service runs as **root** — ZFS introspection needs `/dev/zfs` ioctls — with
namespace-safe hardening only (`PrivateDevices` must stay off).

## Variables

| Variable | Default | Purpose |
|---|---|---|
| `zfs_exporter_port` | `9134` | Listen port (`--web.listen-address`). Must match the scrape target and any host firewall rule. |
| `zfs_exporter_version` | `2.3.12` | Upstream release tag (without the `v`). |
| `zfs_exporter_checksum` | `sha256:…` | Checksum of `zfs_exporter-<version>.linux-amd64.tar.gz` from the release's checksums file. Bump together with the version. |

The exporter discovers pools automatically — there is no pool allowlist.

## Scraping and alerting

Nothing in this role registers a scrape target; add one on the Prometheus side
(a static target / `Endpoints` object pointing at `<host>:9134`). Useful series:
`zfs_pool_state` (non-zero = degraded) and the pool usage gauges — the usual
alerts are pool-degraded plus usage warning/critical thresholds.
