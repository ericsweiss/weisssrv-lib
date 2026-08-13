# weisssrv.infra.node_exporter_host

Installs Prometheus node_exporter on hosts for hardware metrics (thermals via
hwmon/thermal_zone, disk I/O, NIC counters). Listens on **port 9101** so it can
coexist with an in-cluster node-exporter DaemonSet on 9100.

It also runs on guests (LXCs, VMs) purely for their textfile collectors. The
bare-metal-only pieces — `smartmontools`, the `drivetemp` module and the
corosync/zpool/SMART/vzdump collectors — are gated on
**`node_exporter_host_proxmox`**, so a guest installs only the package, the 9101
override and the textfile-collector directory.

## Liveness gate

`node-exporter-healthcheck.timer` fires
`/usr/local/sbin/node-exporter-healthcheck.sh` every
`node_exporter_host_healthcheck_interval` (default `5min`). It GETs
`http://127.0.0.1:<port>/metrics` twice (20s timeout, 5s apart) and, if both
fail while systemd still reports the unit active, restarts
`prometheus-node-exporter` and writes
`node_exporter_healthcheck_last_restart_timestamp_seconds` to the textfile dir.

It exists because the exporter can go **zombie** (`/proc/<pid>/status`
`State: Z`) with its listening socket still bound and nothing accepting: systemd
sees a live main PID, reports `active (running)` forever, and no `Restart=`
policy can fire — the host silently stops being monitored while any
metric-absence alert pages for the wrong reason. `WatchdogSec` cannot cover it
either: the Debian unit is `Type=simple` and node_exporter never `sd_notify`s,
so an HTTP probe is the only trustworthy liveness signal.

A deliberately stopped unit is left alone (`systemctl is-active` guard), so the
gate never fights an operator. Size the interval well inside the `for:` of the
exporter-down alert covering this job, so the self-heal normally lands first.
This is the runtime counterpart to the role's deploy-time `uri` check, which
only proves the exporter was alive at the end of the play.

## Textfile collector

Reads `/var/lib/node_exporter/*.prom` files for custom metrics. Currently
populated by:

- `weisssrv.infra.nas_storage`: media-mover and archive-backupctl run-status
- `weisssrv.infra.acme_certs`: `cert_renewal_*` from renewal/distribution
- application roles: their own backup-freshness metrics
- This role's own corosync + pmxcfs health collector (see below)
- This role's own zpool-status collector (see below)
- This role's own smartmon collector (see below)

## zpool-status collector (hosts with ZFS pools)

This role also installs a per-pool ZFS health collector that runs once a
minute. It exists because pool *health* alone misses silent-corruption: a
single-vdev pool accumulating checksum errors stays `ONLINE` while
`zpool status` quietly counts errors.

Components installed wherever a `zpool` binary is present:

- `/usr/local/sbin/zpool-status-collector.sh` — oneshot script that parses
  `zpool status -v` per pool and writes a `.prom` file atomically.
- `zpool-status-collector.service` + `.timer` — oneshot unit fired every minute.

Emitted metrics (in `/var/lib/node_exporter/zfs_pool_status.prom`):

| Metric | Meaning |
|--------|---------|
| `zfs_pool_status_health_code{pool}` | `0`=ONLINE `1`=DEGRADED `2`=other/FAULTED. |
| `zfs_pool_status_errors_total{pool,type}` | Summed per-vdev READ/WRITE/CKSUM counters; non-zero while still ONLINE is the silent-corruption signature. |
| `zfs_pool_status_data_errors{pool}` | Entries in the `zpool status -v` permanent-error list. |
| `zfs_pool_status_last_scrub_seconds{pool}` | Unix time the last scrub completed (an in-progress scrub counts as fresh; `0` = never). |
| `zfs_pool_status_allocated_bytes{pool}` | Allocated bytes (`zpool list -Hp alloc`). |
| `zfs_pool_status_size_bytes{pool}` | Total pool size in bytes (`zpool list -Hp size`) — emitted only when the pool reports a real size, so a faulted pool can't feed a `0` into the capacity ratio. |
| `zfs_pool_status_collector_last_success_seconds` | Sentinel — staleness means the collector itself is broken. |

Hosts without ZFS emit only the sentinel. Wire the companion alerts
(`ZFSPoolDeviceErrors`, `ZFSPoolDataErrors`, `ZFSPoolNotOnline`,
`ZFSPoolScrubStale`, `ZFSPoolCollectorStale`, plus a capacity warning/critical
pair) in the cluster's alerting rules.

## smartmon collector (Proxmox hosts only)

Exports per-device SMART health to Prometheus every 5 minutes. smartd keeps the
attribute-level **email** path; without this collector no SMART data reaches
Prometheus at all, so dashboards cannot alert on failing drives and ZFS error
events cannot be attributed to a disk.

- `/usr/local/sbin/smartmon-collector.sh` — oneshot script; probes every
  `smartctl --scan` device with `-n standby` so it **never wakes a sleeping
  drive or aborts a long self-test** (the documented reason DEVICESCAN was
  removed from smartd.conf). Writes `smartmon.prom` atomically.
- `smartmon-collector.service` + `.timer` — oneshot unit fired every 5 min.

Emitted metrics (in `/var/lib/node_exporter/smartmon.prom`):

| Metric | Meaning |
|--------|---------|
| `smartmon_device_info{device,model,serial,interface}` | Static identity (always `1`). |
| `smartmon_device_active{device}` | `0` = drive was in standby this cycle (attribute series absent until it wakes — alert expressions should tolerate gaps). |
| `smartmon_device_smart_healthy{device}` | Overall self-assessment: `1`=PASSED/OK, `0`=failing. |
| `smartmon_temperature_celsius{device}` | SMART-reported temperature. |
| `smartmon_reallocated_sector_count{device}` | ATA attr 5 raw. |
| `smartmon_current_pending_sector_count{device}` | ATA attr 197 raw. |
| `smartmon_offline_uncorrectable_count{device}` | ATA attr 198 raw. |
| `smartmon_media_errors_count{device}` | NVMe media/data-integrity errors. |
| `smartmon_collector_last_success_seconds` | Sentinel — staleness means the collector itself is broken. |

Companion alerts to wire up: `SMARTDeviceUnhealthy`,
`SMARTReallocatedSectorsGrowing`, `SMARTPendingSectors`,
`SMARTOfflineUncorrectable`, `SMARTMediaErrors`, `SMARTCollectorStale`.

## Corosync + pmxcfs health collector (Proxmox hosts only)

This role also installs a Proxmox-specific health collector that samples
corosync CPU usage and pmxcfs liveness once a minute, writing the result
to the textfile collector dir.

Components installed on every Proxmox host:

- `/usr/local/sbin/corosync-health-collector.sh` — oneshot script that
  reads `top -bn2` for corosync CPU%, stats
  `/etc/pve/ha/manager_status` for its mtime, and writes a `.prom` file
  atomically.
- `corosync-health-collector.service` — systemd unit (oneshot, `User=root`,
  `After=corosync.service`) that runs the script.
- `corosync-health-collector.timer` — fires the service every minute.

Emitted metrics (in `/var/lib/node_exporter/corosync_health.prom`):

| Metric | Meaning |
|--------|---------|
| `proxmox_corosync_cpu_percent` | CPU% of the corosync process from `top -bn2`'s second sample. Sustained values near 100% indicate a wedged corosync. |
| `proxmox_pmxcfs_manager_status_mtime_seconds` | Unix mtime of `/etc/pve/ha/manager_status` as this node sees it. Compare to `time()` to detect a pmxcfs split-brain (stale local view). `0` if the file does not exist (HA disabled). |
| `proxmox_corosync_health_collector_last_success_seconds` | Unix time the collector itself last completed. Staleness here is a meta-failure — the underlying collector is broken, not corosync/pmxcfs. |

These metrics drive three alerts worth wiring up:

- `CorosyncWedged` — `proxmox_corosync_cpu_percent` pinned high for a
  sustained period (catches corosync alive enough for a host-up alert to stay
  green but no longer processing membership traffic).
- `PmxcfsStale` — `time() - proxmox_pmxcfs_manager_status_mtime_seconds`
  exceeds the staleness budget (the pmxcfs split-brain pattern).
- `CorosyncHealthCollectorStale` — the collector itself hasn't
  succeeded in over five minutes; the other two alerts on this host
  are now serving stale data.

## Configuration

Defaults (`defaults/main.yml`):

```yaml
node_exporter_host_port: 9101              # 9101 to avoid a k3s DaemonSet on 9100
node_exporter_host_textfile_dir: /var/lib/node_exporter
node_exporter_host_proxmox: false          # true on bare-metal Proxmox hosts
node_exporter_host_healthcheck_interval: 5min   # liveness-gate probe period
node_exporter_host_zpool_collector: "{{ node_exporter_host_proxmox }}"
node_exporter_host_corosync_collector: "{{ node_exporter_host_proxmox }}"
node_exporter_host_systemd_collector: true
node_exporter_host_systemd_unit_include: ".+[.](service|timer)"
node_exporter_host_systemd_unit_exclude: ".+[.](automount|device|mount|scope|slice)"
```

Two collectors in the Proxmox block carry their own seam, because "bare-metal
Proxmox" does not imply either fact:

- `node_exporter_host_zpool_collector` — the ZFS-specific one. False on a
  Proxmox host backed by Ceph or LVM-thin, and the zpool script, unit and timer
  are not deployed.
- `node_exporter_host_corosync_collector` — the clustering one. A standalone PVE
  host runs no corosync and has no `/etc/pve/ha/manager_status`, so the
  collector would publish mtime 0, which `PmxcfsStale` treats as stale by
  design; false there ships no emitter instead of a permanently silenced alert.

Both flags also drive the enable+start timer list (built from them rather than
fixed) and reconcile a previously deployed collector away when turned off. The
SMART and vzdump collectors stay on the plain `node_exporter_host_proxmox`
gate.

## systemd collector

node_exporter's systemd collector is **default-off upstream**, so a cluster that
alerts on "unit X failed" without enabling it has an alert with no emitter.
`node_exporter_host_systemd_collector` (default `true`) passes
`--collector.systemd`, `--collector.systemd.enable-restarts-metrics` and the
include/exclude unit filters into the drop-in, producing:

| Metric | Use |
|---|---|
| `node_systemd_unit_state{name,state,type}` | `state="failed"` == 1 is the unit-failed signal |
| `node_systemd_service_restart_total{name}` | crash-loop detection (`increase(...[5m])`); needs the `enable-restarts-metrics` flag, which is passed |
| `node_systemd_units{state}` | per-state unit counts |
| `node_systemd_timer_last_trigger_seconds{name}` | timer staleness |
| `node_systemd_system_running` | `systemctl is-system-running` as a gauge |
| `node_systemd_version` | collector/systemd version info |

`node_exporter_host_systemd_unit_include` is what bounds cardinality: the
collector's unfiltered default enumerates every loaded unit — mounts, slices,
scopes, devices, sockets — which is a large, churny label set with no alerting
value. The default keeps `*.service` and `*.timer`; widen it deliberately if a
rule needs sockets or targets. `node_exporter_host_systemd_unit_exclude` mirrors
upstream's own default as a second guard (both filters are applied).

Both patterns use `[.]` instead of `\.`: the value is written into a systemd
`ExecStart=` line, where a backslash opens a C escape sequence and an
unrecognised one makes systemd reject the command outright.

### Proving the collector actually ran

An HTTP 200 is **not** a per-collector gate. node_exporter answers 200 even when
a collector errors on every scrape: it omits that collector's series and sets
`node_scrape_collector_success{collector="..."}` to `0`. So the role's `/metrics`
health check and `node-exporter-healthcheck.sh` both pass while `node_systemd_*`
is entirely absent — and every alert built on it is *dead*, which looks exactly
like *quiet*.

Two layers close that:

- **Deploy time (this role):** after the health check, an `assert` requires
  `node_systemd_unit_state{` in the scrape body whenever
  `node_exporter_host_systemd_collector` is true. A converge cannot leave a host
  exporting nothing.
- **Runtime (metrics side, not this role):** alert on
  `node_scrape_collector_success{job="<host exporter job>", collector="systemd"} == 0`
  for ~30m at `warning`. It is the only per-collector failure signal node_exporter
  emits, and it names the collector, so it survives relabelling.

The `prometheus-node-exporter` package is installed with `state: present`
(unpinned) and `update_cache: true` (with `cache_valid_time: 3600` to skip a
redundant apt refresh when the cache is under an hour old), so it tracks
whatever the Debian repo currently ships — there is deliberately no version pin.

Scraping it needs a ServiceMonitor (or equivalent) with per-host Endpoints on
port 9101 in the cluster.
