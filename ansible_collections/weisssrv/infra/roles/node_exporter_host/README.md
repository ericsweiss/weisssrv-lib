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
- This role's own slabinfo collector when enabled (see below)

### Alerting over these metrics

A `.prom` file that stops existing yields an empty vector for every series it
held. A rule written only over those series can then never fire, so a disabled
collector, a deleted file or a wiped textfile directory all read as healthy.
Pair any such rule with an existence witness on the same instance:

```
up{job="<host exporter job>"} == 1 unless on (instance) <metric>
```

Each collector here writes its `*_collector_success` line on every run and sets
it to `0` on failure, so a failure is a value rather than an absence. The
corosync collector is the one exception: an unsampleable corosync keeps the
previous file and lets
`proxmox_corosync_health_collector_last_success_seconds` go stale, which is
what `CorosyncHealthCollectorStale` watches.

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
| `zfs_pool_status_errors_total{pool,type}` | The highest READ/WRITE/CKSUM count on any row of the `zpool status` table, so a redundancy-corrected leaf error still shows; non-zero while still ONLINE is the silent-corruption signature. A suffixed count such as `1.2K` is truncated to its integer part, so treat this series as zero or non-zero, not as a magnitude. |
| `zfs_pool_status_data_errors{pool}` | Entries in the `zpool status -v` permanent-error list. |
| `zfs_pool_status_last_scrub_seconds{pool}` | Unix time the last scrub or resilver completed. A scan in progress counts as fresh, so `ZFSPoolScrubStale` does not fire mid-scan on a long pool. `0` = never. |
| `zfs_pool_status_allocated_bytes{pool}` | Allocated bytes (`zpool list -Hp alloc`). |
| `zfs_pool_status_size_bytes{pool}` | Total pool size in bytes (`zpool list -Hp size`) — emitted only when the pool reports a real size, so a faulted pool can't feed a `0` into the capacity ratio. |
| `zfs_pool_status_collector_success` | `1` when `zpool` ran and reported at least one pool, `0` when nothing was measured. |
| `zfs_pool_status_collector_last_success_seconds` | Sentinel — staleness means the collector itself is broken. |

A host with no pools publishes the sentinel and `..._collector_success 0`, so a
host without ZFS and a host whose pools all vanished are distinguishable. Wire
the companion alerts (`ZFSPoolDeviceErrors`, `ZFSPoolDataErrors`,
`ZFSPoolNotOnline`, `ZFSPoolScrubStale`, `ZFSPoolCollectorStale`, an arm on
`zfs_pool_status_collector_success == 0`, plus a capacity warning/critical pair)
in the cluster's alerting rules.

## slabinfo collector (Proxmox hosts only, opt-in)

`node_memory_Slab_bytes` only carries the total, so a kernel slab leak is
invisible until the host runs out of memory and nothing says which cache is
growing. This collector publishes per-cache numbers for an allowlist, which is
what turns "slab is growing" into a named tenant and, once the growth stops, a
closing condition.

This is the role's own textfile collector writing `node_slabinfo.prom`, not
node-exporter's built-in `--collector.slabinfo`. The built-in one emits
`node_slabinfo_objects*` and `node_slabinfo_slabs*` series labelled `slab`, and
enabling it produces no `node_slab_object_bytes{cache}` at all. Cite the
textfile collector by name in consumer docs and alerts.

Off by default (`node_exporter_host_slabinfo_collector`), and the cache list
(`node_exporter_host_slab_caches`) is empty, because each cache adds scrape
series. Set both on the host being investigated. It ships inside the Proxmox-host textfile collectors, so
`node_exporter_host_proxmox` must be true as well; the role fails the play if it
is not, rather than converging green and installing nothing. An empty
`node_exporter_host_slab_caches` with the collector enabled fails the play for
the same reason: it would publish only the collector sentinel.

- `/usr/local/sbin/slabinfo-collector.sh` — oneshot script reading
  `/proc/slabinfo`, writing `node_slabinfo.prom` atomically every 5 minutes.
- `/etc/default/slabinfo-collector` — the rendered allowlist (`SLAB_CACHES`).

Emitted metrics (in `/var/lib/node_exporter/node_slabinfo.prom`):

| Metric | Meaning |
|--------|---------|
| `node_slab_objects{cache}` | Live objects in the cache (`active_objs`). |
| `node_slab_object_bytes{cache}` | Live bytes (`active_objs * objsize`) — the series to trend for a leak. |
| `node_slab_pages{cache}` | Pages backing the cache (`num_slabs * pagesperslab`). |
| `node_slabinfo_collector_success` | `0` when `/proc/slabinfo` was unreadable (unprivileged container, kernel without the option). Emitted on every run, so an absence rule still fires. |
| `node_slab_cache_present{cache}` | `0` when the named cache is absent from `/proc/slabinfo`. |
| `node_slabinfo_collector_last_success_seconds` | Emitted only on a successful run — staleness means the collector itself is broken. |

A cache absent from `/proc/slabinfo` (a typo, or a name the kernel renamed or
merged away without `slub_nomerge`) reads exactly like a leak that stopped.
`node_slab_cache_present{cache} == 0` is the series that says so.

A mergeable cache only appears under its own name once `slub_nomerge` is on the
kernel command line. On a stock kernel `node_slab_cache_present{cache} == 0`
therefore means the name needs picking again, not that the leak stopped. The
default list is empty, so name the caches on the host under investigation.

The timer runs the collector with no arguments, so the allowlist comes from
`SLAB_CACHES` in `/etc/default/slabinfo-collector`. `SLABINFO_CONF` and
`SLABINFO` override that file and `/proc/slabinfo` for tests; the unit sets
neither.

### Recommended alert

A growth rule on `node_slab_object_bytes` is silent when the producer stops: a
dead timer, an unreadable `/proc/slabinfo` or a cache the kernel renamed all
read as "the leak stopped". Wire this witness alongside any such rule, in the
cluster's alerting rules:

```
node_slabinfo_collector_success == 0
  or (time() - node_slabinfo_collector_last_success_seconds > 1800)
  or min by (instance, cache) (node_slab_cache_present) == 0
```

`for: 30m`, severity warning. Scope each selector to the job that scrapes this
exporter.

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
| `smartmon_collector_success` | `1` when `smartctl` ran and enumerated at least one device, `0` when nothing was measured. |
| `smartmon_collector_last_success_seconds` | Sentinel — staleness means the collector itself is broken. |

Companion alerts to wire up: `SMARTDeviceUnhealthy`,
`SMARTReallocatedSectorsGrowing`, `SMARTPendingSectors`,
`SMARTOfflineUncorrectable`, `SMARTMediaErrors`, `SMARTCollectorStale`, and an
arm on `smartmon_collector_success == 0`.

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

## vzdump metrics hook (Proxmox hosts only)

`vzdump-metrics-hook.sh` is deployed to every Proxmox host and wired into the
backup job's `script` property. It writes one textfile after each job:

| Metric | Meaning |
|--------|---------|
| `vzdump_backup_last_run_success` | `1` if the last run backed up every guest it started, `0` if any guest aborted. |
| `vzdump_backup_guests` | Guests the last run started backing up on this node. `0` means the job had nothing to do here, so a staleness alert on the timestamp is about the job, not about backups. |
| `vzdump_backup_last_success_timestamp_seconds` | Unix time of the last fully successful run. **Absent** until the first success, so a staleness rule needs an absence arm rather than reading a `0` as 1970. |

Gate a backup-staleness alert on `vzdump_backup_guests > 0`: a node whose guests
are all excluded from the job still runs the hook, and the timestamp alone
cannot tell "backups are late" from "this node backs up nothing".

The textfile persists between runs, so every series above describes the last run
and nothing else. Once a schedule stops selecting guests on a node,
`vzdump_backup_guests` stays `0` until some later run picks guests up again. An
alert that EXCLUDES a host on `vzdump_backup_guests == 0` must therefore pair
that with `vzdump_backup_last_run_success == 1`. Without the second arm, a node
whose last run both failed and started no guest is permanently exempt from
escalation.

vzdump owns the hookscript's first argument (the phase), so the output
directory travels in `/etc/default/vzdump-metrics-hook`, rendered from
`node_exporter_host_textfile_dir`.

## Configuration

Defaults (`defaults/main.yml`):

```yaml
node_exporter_host_port: 9101              # 9101 to avoid a k3s DaemonSet on 9100
node_exporter_host_bind_address: ""        # "" = all interfaces
node_exporter_host_textfile_dir: /var/lib/node_exporter
node_exporter_host_proxmox: false          # true on bare-metal Proxmox hosts
node_exporter_host_healthcheck_interval: 5min   # liveness-gate probe period
node_exporter_host_zpool_collector: "{{ node_exporter_host_proxmox }}"
node_exporter_host_corosync_collector: "{{ node_exporter_host_proxmox }}"
node_exporter_host_slabinfo_collector: false
node_exporter_host_slab_caches: []          # names come from /proc/slabinfo
node_exporter_host_systemd_collector: true
node_exporter_host_systemd_unit_include: ".+[.](service|timer)"
node_exporter_host_systemd_unit_exclude: ".+[.](automount|device|mount|scope|slice)"
node_exporter_host_processes_collector: false   # node_processes_* (walks /proc)
```

The exporter serves unauthenticated plaintext HTTP.
`node_exporter_host_bind_address` narrows the listener: leave it empty to listen
on every interface and rely on a host firewall, or set the scrape-facing address
where there is none. The role's own liveness probe and the healthcheck timer
follow it, and an IPv6 literal is bracketed for you.

Three collectors in the Proxmox block carry their own seam, because "bare-metal
Proxmox" does not imply any of these facts:

- `node_exporter_host_zpool_collector` — the ZFS-specific one. False on a
  Proxmox host backed by Ceph or LVM-thin, and the zpool script, unit and timer
  are not deployed.
- `node_exporter_host_corosync_collector` — the clustering one. A standalone PVE
  host runs no corosync and has no `/etc/pve/ha/manager_status`, so the
  collector would publish mtime 0, which `PmxcfsStale` treats as stale by
  design; false there ships no emitter instead of a permanently silenced alert.
- `node_exporter_host_slabinfo_collector` — the per-cache slab one, off by
  default and turned on per host while a slab leak is being attributed.

All three flags also drive the enable+start timer list (built from them rather
than fixed) and reconcile a previously deployed collector away when turned off. The
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

## processes collector

`node_exporter_host_processes_collector` (default `false`) passes
`--collector.processes`, the only source of `node_processes_*`. The in-cluster
node-exporter DaemonSet usually enables it, so the node-exporter-full
dashboard's System Processes rows are populated for `job=node-exporter` but
empty for this role's job until the flag is set. The reverse holds for the
Systemd rows, which only this role emits.

| Metric | Use |
|---|---|
| `node_processes_pids` | processes currently running |
| `node_processes_state{state}` | per-state process counts |
| `node_processes_threads` | threads currently running |
| `node_processes_max_processes` | kernel pid limit |

It is off by default because the collector walks every entry under `/proc` on
each scrape, which is a real cost on a busy Proxmox host.

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

## vzdump backup metrics

`files/vzdump-metrics-hook.sh` publishes `vzdump_backup_last_run_success`,
`vzdump_backup_last_success_timestamp_seconds` and `vzdump_backup_guests` to the
textfile collector. It is wired through the `jobs.cfg` `script` property but is
deployed to every Proxmox host, because a cluster-wide `all` job runs on each
node for its local guests and a node missing the hook aborts its own backups.
The `job-end` phase fires even when individual guests failed, so a per-run
marker records any abort and downgrades the job-end verdict.
