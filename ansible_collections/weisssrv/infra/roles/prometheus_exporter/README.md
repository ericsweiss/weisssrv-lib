# weisssrv.infra.prometheus_exporter

Shared install pipeline for download-based Prometheus exporters. It owns the
boilerplate that drifts across exporter roles: probe the installed version,
conditionally download the release artifact, install it, then enable + start
the service and health-check it.

Two artifact types are supported:

- `tarball` — `get_url` the `.tar.gz`, `unarchive` it, copy the binary out.
- `deb` — `get_url` the `.deb`, install it with `apt: deb:`.

Backs **`zfs_exporter`** (tarball) and **`unbound_exporter`** (deb). Both are
thin wrappers that pass their specifics as `vars` and keep their own
`templates/<name>.service.j2` unit file.

**`adguard_sync`** (adguardhome-sync) is a partial consumer: it runs only the
**install** half (tarball + `checksums.txt`), then manages its own oneshot
service and timer directly. The `service` half is unused — adguardhome-sync is
timer-driven, not a long-running listener, so there is no port to health-check.
It reuses the shared `Restart prometheus exporter` handler but points
`prometheus_exporter_service_name` at `adguardhome-sync.timer`, so a fresh
binary is picked up on the timer's next tick instead of by (re)starting a
daemon.

## Roles that stay standalone

Two exporter-adjacent roles deliberately do not use this pipeline:

- `node_exporter_host` installs from the Debian apt repo, not a release
  download, so none of the download/checksum/extract/version-probe pipeline
  applies.
- `adguard_home` downloads a tarball but must stop the running service
  mid-pipeline before swapping the binary, which this role's install-then-start
  flow cannot express. `adguard_sync` has no such need and reuses the install
  half.

## How wrappers invoke it

The wrapper does not include the whole role. It runs the two halves around its
own unit template so a unit change is deployed before the service is restarted:

1. `include_role: { name: prometheus_exporter, tasks_from: install }` with the
   param block in `vars:` — probe + conditional download + install.
2. `template:` the wrapper's own `<name>.service.j2`, notifying the shared
   `Restart prometheus exporter` handler.
3. `include_role: { name: prometheus_exporter, tasks_from: service }` with the
   same `vars:` — enable + start + `flush_handlers` + health check.

`tasks/main.yml` chains install + service for standalone runs (the molecule
scenario), and the shared handler restarts `prometheus_exporter_service_name`.

## Parameters

| Variable | Meaning | zfs_exporter | unbound_exporter |
|---|---|---|---|
| `prometheus_exporter_name` | Title-only logical name | `zfs_exporter` | `unbound_exporter` |
| `prometheus_exporter_version` | Pinned upstream version | `{{ zfs_exporter_version }}` | `{{ unbound_exporter_version }}` |
| `prometheus_exporter_artifact_type` | `tarball` or `deb` | `tarball` | `deb` |
| `prometheus_exporter_download_url` | Release artifact URL | GitHub tarball | GitHub `.deb` |
| `prometheus_exporter_checksum` | `get_url` checksum; empty = none | `{{ zfs_exporter_checksum }}` | `{{ unbound_exporter_checksum }}` |
| `prometheus_exporter_binary_path` | Installed binary (tarball) | `/usr/local/bin/zfs_exporter` | `/usr/bin/unbound_exporter` (deb-managed) |
| `prometheus_exporter_archive_member` | Binary path inside the tarball | `zfs_exporter-<ver>.linux-amd64/zfs_exporter` | _(unused)_ |
| `prometheus_exporter_version_check_cmd` | Shell printing installed version | `--version` + awk | `dpkg-query` + sed |
| `prometheus_exporter_service_name` | systemd unit (no `.service`) | `zfs-exporter` | `unbound-exporter` |
| `prometheus_exporter_port` | Health-check port | `{{ zfs_exporter_port }}` | `{{ unbound_exporter_port }}` |
| `prometheus_exporter_tmp_dir` | Scratch dir for download/extract | `/var/cache/prometheus_exporter` | `/var/cache/prometheus_exporter` |

The install half asserts every parameter above except
`prometheus_exporter_checksum`, `prometheus_exporter_archive_member` (tarball
only), `prometheus_exporter_tmp_dir` and `prometheus_exporter_port`. The service
half asserts `prometheus_exporter_port`, its only consumer. A mistyped var name
therefore fails the play with the parameter named instead of an empty URL
several tasks later. An empty `prometheus_exporter_checksum` means no
verification; every caller in this collection pins a sha256.

The version-check command is the source of truth for idempotence: empty stdout
or a non-zero rc means "(re)install"; a stdout matching
`prometheus_exporter_version` (after `trim`) means "skip".

## See also

- `weisssrv.infra.zfs_exporter`, `weisssrv.infra.unbound_exporter` — the wrappers
- `weisssrv.infra.textfile_collector` — for script-emitted metrics instead
