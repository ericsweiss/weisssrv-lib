# Role: unbound

Installs Unbound as a **forwarding** DNS resolver: every query leaves over
DNS-over-TLS to the configured public upstreams via a `forward-zone: name: "."`.
It never recurses from the root. It listens on loopback only, as the upstream
for a LAN-facing filtering resolver (`weisssrv.infra.adguard_home`).

## What it manages

- The role-owned drop-in `/etc/unbound/unbound.conf.d/<unbound_dropin_name>`:
  listen addresses, cache/threading tuning, privacy hardening, DNSSEC
  validation, private-address rebinding protection, access control, and the
  DoT forward zone.
- `/etc/unbound/unbound.conf.d/remote-control.conf`: `unbound-control` over the
  local Unix socket `/run/unbound.ctl` with `control-use-cert: no`, which is why
  no control certificate pair is generated. `weisssrv.infra.unbound_exporter`
  reads stats through that socket.
- Removal of superseded role-owned drop-ins (`unbound_legacy_dropins`), then a
  readiness probe: `unbound_interface`:`unbound_port` must accept connections
  and `dig @unbound_interface` must return an A record for
  `unbound_probe_name`.

`/etc/resolv.conf` is deliberately untouched — this resolver is not on port 53.

## Inputs

| Variable | Default | Purpose |
|---|---|---|
| `unbound_port` | `5335` | Listen port; off 53 so a filtering resolver can own 53 |
| `unbound_interface` | `127.0.0.1` | Readiness-probe target and sole default listen address |
| `unbound_interfaces` | `[unbound_interface]` | Full listen list; add `::1` here to also serve IPv6 loopback |
| `unbound_probe_name` | `google.com` | Name the readiness probe resolves; point it at a name the configured forwarders answer when the host has no public egress |
| `unbound_access_control` | `["127.0.0.0/8 allow"]` | ACL lines; must cover what `unbound_interfaces` serves — every other netblock is refused |
| `unbound_dropin_name` | `managed.conf` | Filename of the role-owned drop-in |
| `unbound_legacy_dropins` | `["weisssrv.conf"]` | Drop-in names removed on every run; unbound merges the directory with a **sorted** glob, so a survivor that sorts after the managed file wins every duplicated scalar |
| `unbound_forwarders` | Cloudflare / Quad9 / Google, port 853 | DoT upstreams (`addr`, `port`, `name`); `forward-tls-upstream` is always on |
| `unbound_num_threads` | `2` | |
| `unbound_msg_cache_size` / `unbound_rrset_cache_size` | `16m` / `32m` | Sized for a 2 GB resolver guest |
| `unbound_outgoing_range` / `unbound_num_queries_per_thread` | `8192` / `4096` | |
| `unbound_so_reuseport` | `true` | |
| `unbound_serve_expired` / `unbound_serve_expired_ttl` | `true` / `86400` | Serve stale answers while refreshing |
| `unbound_aggressive_nsec` | `true` | |
| `unbound_edns_buffer_size` | `1232` | |
| `unbound_use_caps_for_id` | `true` | 0x20 randomization; redundant behind DoT and a SERVFAIL source against upstreams that normalize case — set `false` if an upstream misbehaves |

`cache-min-ttl` (60s), `cache-max-ttl` (86400s), `prefetch`, and the
`private-address` list are fixed in the template, not variables.

## Query path

```
clients → filtering resolver (:53) → unbound (127.0.0.1:5335) → DoT :853 upstreams
```

## Operations

```bash
unbound-checkconf /etc/unbound/unbound.conf.d/managed.conf
dig @127.0.0.1 -p 5335 example.com +dnssec
unbound-control stats_noreset
unbound-control flush_zone .
journalctl -u unbound -f
```

If Unbound will not start, `unbound-checkconf` on the drop-in names the offending
line; if queries fail, confirm the socket with `ss -tlnp | grep 5335` and the DoT
path with `openssl s_client -connect 1.1.1.1:853`.
