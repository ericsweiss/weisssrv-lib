# Role: zfs_exporter

Installs the [pdf/zfs_exporter](https://github.com/pdf/zfs_exporter) on
pve-nas-01 to expose ZFS pool/dataset metrics (health, usage, scrub status,
fragmentation) to Prometheus.

Runs as root because ZFS introspection requires root on Linux.

## Configuration

```yaml
zfs_exporter_port: 9134
```

`zfs_exporter_version` + `zfs_exporter_checksum` pin the upstream release;
bump them together. The exporter discovers ZFS pools automatically — there is
no pool allowlist to configure.

Useful series for alerting: `zfs_pool_state` (non-zero = degraded) and the
pool usage gauges.
