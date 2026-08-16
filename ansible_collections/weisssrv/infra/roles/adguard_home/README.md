# weisssrv.infra.adguard_home

Installs and reconciles an AdGuard Home DNS filtering / ad-blocking server.

Run it on every instance. Set `adguard_home_is_primary: true` on exactly one:
rewrites and filtering rules are reconciled there and replicated by
`weisssrv.infra.adguard_sync`, so a second reconciler would fight the sync.

## What this role manages

### Via API (idempotent)

**Base configuration** (`api_base_config.yml`) — on EVERY instance, since each
needs its own upstreams and TLS:
- DNS filtering/ad-blocking protection
- Upstream DNS servers (a local recursive resolver by default)
- Upstream mode (load balancing, parallel, or fastest)
- Fallback DNS servers
- DNSSEC validation
- Client reverse DNS resolution (rDNS)
- IPv6 DNS support
- Rate limiting (requests/s per client)
- Rate limit whitelist
- TLS/DoT/DoQ configuration (HTTPS, DoT, DoQ ports)
- DNS cache size + TTL bounds
- Cache optimistic mode
- DHCP server enable/disable

**DNS records** (`api_config.yml`) — **primary only**:
- DNS rewrites (forward A records)
- Custom filtering rules (reverse PTR records)
- Reconciliation: adds missing, deletes orphaned — but only for a **non-empty**
  codified list. An empty `adguard_home_rewrites` / `_user_rules` means "manage
  none" and the live state is left untouched, so a dropped group_var cannot wipe
  the resolver. Set `adguard_home_prune_rewrites` / `_prune_user_rules` to make
  the empty list authoritative (that is how the last record is removed).

### Admin password (upstream limitation)

AdGuard Home has no password API (`/control/profile/update` takes only
name/language/theme), so the admin's bcrypt hash is reconciled in
`AdGuardHome.yaml` by `files/adguard-admin-hash.py`, installed on the host at
`adguard_home_hash_helper_path`:

```bash
adguard-admin-hash.py --config <install_path>/AdGuardHome.yaml --user <user> read
printf '%s' "$password" | adguard-admin-hash.py --config … --user <user> reconcile
```

- it **parses** the document to locate
  `users[?name == adguard_home_admin_user].password`. A line-oriented edit
  (`grep`/`lineinfile` for an indented `password:`) means "the last password key
  in the file" — a different account's the moment a second user exists.
- it rewrites **only that one line** (temp file + atomic replace, mode/uid/gid
  preserved) and re-parses the result before committing. AdGuard owns this file
  at runtime; re-emitting it from parsed YAML would reformat everything the role
  did not write.
- it prints `UNCHANGED` when the stored hash already verifies against the
  password, so a converged host is untouched and nothing restarts. `CHANGED`
  notifies the restart handler. (Compare those verdicts exactly — `UNCHANGED`
  contains `CHANGED`.)
- the password arrives on **stdin**, never in argv or `environment:` (Ansible
  prefixes environment assignments onto the remote command string, where they
  are readable in `/proc` for the length of the run).

### Not managed

- HTTP/DNS port changes after setup (they need a service restart)
- Filter lists (managed in the UI)
- The DHCP server: only the disable direction is implemented, so
  `adguard_home_dhcp_enabled: true` is rejected by an assert rather than
  silently ignored

## Variables

| Variable | Meaning | Required |
|---|---|---|
| `adguard_home_version` | Pinned AdGuard Home release | yes |
| `adguard_home_admin_password` | Admin password (secret store) | yes |
| `adguard_home_tls_server_name` | DoT/DoH/DoQ server name; must match the distributed cert. Asserted at role entry when TLS is enabled | yes when `adguard_home_tls_enabled` |
| `adguard_home_admin_user` | Admin account name in `users[]` | no (`admin`) |
| `adguard_home_is_primary` | Reconcile rewrites + filtering rules here (exactly one instance) | no (`false`) |
| `adguard_home_tls_enabled` | Configure TLS once certs are present | no (`true`) |
| `adguard_home_cert_path` | Where `acme_certs` delivers `fullchain.pem` / `privkey.pem` | no (`<install_path>/certs`) |
| `adguard_home_upstream_dns` | Upstream resolvers | no (`127.0.0.1:5335`) |
| `adguard_home_rewrites` / `_user_rules` | Primary-only API-managed records; empty means "manage none" | no (`[]`) |
| `adguard_home_prune_rewrites` / `_prune_user_rules` | Treat the empty list as authoritative and delete what it does not name | no (`false`) |
| `adguard_home_web_bind` / `_dns_bind` | Listen addresses written by the first-install wizard | no (`0.0.0.0`) |
| `adguard_home_after_units` / `_wants_units` | Extra systemd ordering for the upstream resolver | no (`[unbound.service]`) |
| `adguard_home_dns_probe_name` | Name resolved by the post-deploy smoke test | no (`google.com`) |
| `adguard_home_archive_cache_dir` | Local mirror holding `AdGuardHome_linux_<arch>-v<version>.tar.gz`; used instead of the GitHub download when present. Empty disables the lookup | no (`""`) |
| `adguard_home_archive_sha256` | sha256 the staged archive must match; empty falls back to a `checksums.txt` staged in the same directory | no (`""`) |
| `adguard_home_skip_api_config` | Skip password + API reconciliation | no (`false`) |
| `adguard_home_skip_resolv_conf_update` | Leave `/etc/resolv.conf` alone | no (`false`) |
| `adguard_home_user` / `_group` / `_install_path` | Service identity and prefix | no (`adguard`, `adguard`, `/opt/AdGuardHome`) |
| `adguard_home_hash_helper_path` | Where the password helper is installed | no (`/usr/local/sbin/adguard-admin-hash.py`) |
| `adguard_home_settle_seconds` | Pause between config rewrite and restart (container harnesses only) | no (`0`) |
| `adguard_home_use_private_tmp` / `_use_protect_system` | systemd sandboxing (disable in containers) | no (`true`) |

The remaining knobs (ports, cache, rate limiting, DHCP, DNSSEC, fallbacks) are
listed with their defaults in `defaults/main.yml`.

## Configuration

```yaml
# Base settings
adguard_home_http_port: 3000
adguard_home_dns_port: 53
adguard_home_dot_port: 853
adguard_home_https_port: 443
adguard_home_doq_port: 853

# Upstream DNS (points to Unbound)
adguard_home_upstream_dns:
  - "127.0.0.1:5335"

# DNS features
adguard_home_protection_enabled: true
adguard_home_upstream_mode: "load_balance"  # load_balance, parallel, or fastest_addr
adguard_home_fallback_dns: []
adguard_home_enable_dnssec: true
adguard_home_resolve_clients: true
adguard_home_use_private_ptr_resolvers: false  # false when using static PTR records
adguard_home_disable_ipv6: false

# TLS/Encryption configuration
adguard_home_tls_enabled: true
adguard_home_tls_server_name: "dns.example.net"   # must match the distributed cert
adguard_home_cert_path: "{{ adguard_home_install_path }}/certs"

# Cache configuration
adguard_home_cache_enabled: true
adguard_home_cache_size: 8388608  # 8MB cache
adguard_home_cache_ttl_min: 0
adguard_home_cache_ttl_max: 0
adguard_home_cache_optimistic: false

# Rate limiting
adguard_home_ratelimit: 20
adguard_home_ratelimit_whitelist: []

# DHCP (must stay false — see "Not managed")
adguard_home_dhcp_enabled: false

# DNS rewrites (managed via API)
adguard_home_rewrites:
  - domain: "app.example.net"
    answer: "10.0.0.20"

# Custom filtering rules (managed via API)
adguard_home_user_rules:
  - '||10.0.0.5.in-addr.arpa^$dnsrewrite=NOERROR;PTR;app.example.net.'
```

## Architecture

```
primary  (adguard_home_is_primary: true)
  ├─ base settings reconciled via API
  ├─ DNS rewrites added/deleted
  └─ filtering rules replaced
        │  weisssrv.infra.adguard_sync (timer)
        ▼
replica  (base settings reconciled locally; rewrites/rules arrive by sync)
```

## API endpoints used

- `GET /control/dns_info` - Get DNS configuration
- `POST /control/dns_config` - Update DNS configuration
- `GET /control/tls/status` - Get TLS status
- `POST /control/tls/configure` - Configure TLS
- `GET /control/rewrite/list` - List DNS rewrites
- `POST /control/rewrite/add` - Add DNS rewrite
- `POST /control/rewrite/delete` - Delete DNS rewrite
- `GET /control/filtering/status` - Get filtering rules
- `POST /control/filtering/set_rules` - Replace filtering rules

## Files

- `tasks/main.yml` - install, service, admin password, orchestration
- `tasks/api_base_config.yml` - base settings management via API
- `tasks/api_config.yml` - DNS records management via API (primary only)
- `files/adguard-admin-hash.py` - reads/reconciles the admin's bcrypt hash
- `templates/adguardhome.service.j2` - systemd service
- `handlers/main.yml` - service restart handler

## Dependencies

`meta/main.yml` declares none — ordering is the playbook's job, so pointing
`adguard_home_upstream_dns` at a public resolver really does drop the resolver
below rather than installing it anyway.

- `weisssrv.infra.unbound` — the default upstream resolver on 127.0.0.1:5335.
  **Apply it before this role** at the default upstream: the post-deploy dig
  probe resolves through it. `adguard_home_after_units` / `_wants_units` carry
  the matching systemd ordering, and go empty alongside it.
- `weisssrv.infra.acme_certs` — distributes the TLS material
- `weisssrv.infra.adguard_sync` — replicates primary → replica

## Security

- All API calls use HTTP Basic Auth with the configured admin credentials
- API calls use `no_log: true` to prevent credential exposure
- Runs as unprivileged `adguard` user with `CAP_NET_BIND_SERVICE`
- Config file owned by `adguard:adguard` with mode `0600`
- The admin UI is **plaintext HTTP**: `force_https` stays false so the role can
  reconcile over the localhost API, and the wizard binds
  `adguard_home_web_bind` (`0.0.0.0` by default). Restrict that bind address or
  firewall `adguard_home_http_port` to trusted networks.
- On a **fresh host** there is a window between the service starting and the
  role's `/control/install/configure` POST in which the setup wizard is reachable
  on that bind address and takes **no credentials** — the instance has none yet.
  Whoever reaches the port first sets the admin password. Provision fresh
  resolvers behind the firewall rules, or bind the wizard to loopback for the
  first run and widen `adguard_home_web_bind` afterwards.
- The archive cache is opt-in (`adguard_home_archive_cache_dir` is empty by
  default) and is treated as a **root trust boundary**, because its contents are
  unpacked and installed as root with no upstream signature. Before any digest is
  compared, the role asserts that the cache directory, the staged archive, and
  the `checksums.txt` (when one is consulted) are each owned by **root (uid 0)**
  and **not writable by group or other**; the play fails naming the offending
  path if not. This is checked ahead of the verification rather than as part of
  it: a digest only proves the bytes match a value that lives in the same
  directory, so a writer who can swap the archive can swap the `checksums.txt`
  beside it, and the comparison would still pass.
- Only then is the staged archive verified — against `adguard_home_archive_sha256`
  if set, otherwise against a `checksums.txt` staged in the same directory — and
  the play fails if neither is available. Setting the pin is the stronger
  statement: it lives in inventory rather than in the directory being trusted.
