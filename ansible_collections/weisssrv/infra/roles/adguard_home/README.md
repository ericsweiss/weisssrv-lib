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
- Rate limit subnet lengths (the bucket the per-client count is kept over)
- TLS/DoT/DoQ configuration (HTTPS, DoT, DoQ ports)
- DNS cache size + TTL bounds
- Cache optimistic mode
- DHCP server kept disabled

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

- It **parses** the document to locate
  `users[?name == adguard_home_admin_user].password`, so a second user's key is
  never touched.
- It rewrites **only that one line** through a temp file and an atomic replace,
  preserving mode, uid and gid. It re-parses the result before committing.
  AdGuard owns the rest of the file at runtime.
- It prints `UNCHANGED` when the stored hash already verifies against the
  password, so a converged host is untouched and nothing restarts. `CHANGED`
  notifies the restart handler. Compare the verdicts exactly, because
  `UNCHANGED` contains `CHANGED`.
- The password arrives on **stdin**, never in argv or `environment:`. Ansible
  prefixes environment assignments onto the remote command string, which
  `/proc` exposes for the length of the run.

It exits 0 on success and 1 on any error: config unreadable or unparsable, the
user absent or ambiguous, no password key, a multi-line scalar, passlib
missing, or post-write verification failing.

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
| `adguard_home_rewrites`, `adguard_home_user_rules` | Primary-only API-managed records; empty means "manage none" | no (`[]`) |
| `adguard_home_prune_rewrites`, `adguard_home_prune_user_rules` | Treat the empty list as authoritative and delete what it does not name | no (`false`) |
| `adguard_home_http_port`, `adguard_home_dns_port`, `adguard_home_dot_port`, `adguard_home_https_port`, `adguard_home_doq_port` | Admin UI, DNS, DoT, HTTPS and DoQ ports | no (`3000`, `53`, `853`, `443`, `853`) |
| `adguard_home_protection_enabled`, `adguard_home_enable_dnssec`, `adguard_home_resolve_clients` | Filtering, DNSSEC and client rDNS | no (`true`) |
| `adguard_home_upstream_mode` | `load_balance`, `parallel` or `fastest_addr` | no (`load_balance`) |
| `adguard_home_fallback_dns` | Fallback resolvers | no (`[]`) |
| `adguard_home_use_private_ptr_resolvers` | Forward private PTR lookups; keep false with static PTR records | no (`false`) |
| `adguard_home_disable_ipv6` | Drop AAAA answers | no (`false`) |
| `adguard_home_cache_enabled`, `adguard_home_cache_size`, `adguard_home_cache_ttl_min`, `adguard_home_cache_ttl_max`, `adguard_home_cache_optimistic` | DNS cache: on/off, size in bytes, TTL bounds, optimistic mode | no (`true`, `8388608`, `0`, `0`, `false`) |
| `adguard_home_ratelimit`, `adguard_home_ratelimit_whitelist` | Requests/s per client, and clients exempt from it | no (`20`, `[]`) |
| `adguard_home_ratelimit_subnet_len_ipv4`, `adguard_home_ratelimit_subnet_len_ipv6` | Subnet the per-client rate limit is counted over. At the default 24, one non-whitelisted client consumes the bucket for its whole /24, so a per-host `adguard_home_ratelimit_whitelist` only has bite at 32. Needs an AdGuard that reports the fields (0.107.4x line or newer); one that does not fails the play rather than dropping the value | no (`24`, `56`) |
| `adguard_home_web_bind`, `adguard_home_dns_bind` | Listen addresses written by the first-install wizard | no (`0.0.0.0`) |
| `adguard_home_after_units`, `adguard_home_wants_units` | Extra systemd ordering for the upstream resolver | no (`[unbound.service]`) |
| `adguard_home_dns_probe_name` | Name resolved by the post-deploy smoke test | no (`google.com`) |
| `adguard_home_archive_cache_dir` | Local mirror holding `AdGuardHome_linux_<arch>-v<version>.tar.gz`; used instead of the GitHub download when present. Empty disables the lookup | no (`""`) |
| `adguard_home_stage_dir` | Root-owned 0700 directory the release tarball is downloaded and unpacked in, on disk rather than a tmpfs. Removed after the install | no (`/root/adguard-home-install`) |
| `adguard_home_archive_sha256` | sha256 the staged archive must match; empty falls back to a `checksums.txt` staged in the same directory | no (`""`) |
| `adguard_home_skip_api_config` | Skip password + API reconciliation | no (`false`) |
| `adguard_home_skip_resolv_conf_update` | Leave `/etc/resolv.conf` alone | no (`false`) |
| `adguard_home_user`, `adguard_home_group`, `adguard_home_install_path` | Service identity and prefix | no (`adguard`, `adguard`, `/opt/AdGuardHome`) |
| `adguard_home_hash_helper_path` | Where the password helper is installed | no (`/usr/local/sbin/adguard-admin-hash.py`) |
| `adguard_home_settle_seconds` | Pause between config rewrite and restart (container harnesses only) | no (`0`) |
| `adguard_home_use_private_tmp`, `adguard_home_use_protect_system` | systemd sandboxing (disable in containers) | no (`true`) |

Every default, with its comment, is in `defaults/main.yml`.

## Configuration

The values a consumer sets per site:

```yaml
adguard_home_version: "0.107.0"
adguard_home_tls_server_name: "dns.example.net"   # must match the distributed cert
adguard_home_is_primary: true                       # exactly one instance

# DNS rewrites (managed via API, primary only)
adguard_home_rewrites:
  - domain: "app.example.net"
    answer: "10.0.0.20"

# Custom filtering rules (managed via API, primary only)
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
- The admin UI is **plaintext HTTP**. Restrict `adguard_home_web_bind` or
  firewall `adguard_home_http_port` to trusted networks, and use the `:443`
  endpoint by hostname elsewhere. `force_https` stays false because the role
  reconciles over the plaintext admin API, and the certificate does not cover
  `127.0.0.1`. The role dials the address `adguard_home_web_bind` names, or
  loopback for a wildcard bind, so a restricted bind keeps reconcile working.
- On a **fresh host** there is a window between the service starting and the
  role's `/control/install/configure` POST in which the setup wizard is reachable
  on that bind address and takes **no credentials** — the instance has none yet.
  Whoever reaches the port first sets the admin password. Provision fresh
  resolvers behind the firewall rules, or bind the wizard to loopback for the
  first run and widen `adguard_home_web_bind` afterwards.
- Set `adguard_home_archive_sha256` when the archive cache is in use. The pin
  lives in inventory rather than in the directory being trusted.
- The archive cache is opt-in and is a **root trust boundary**: its contents
  are installed as root with no upstream signature. Before any digest compare,
  the role asserts that the cache directory, the staged archive and any
  consulted `checksums.txt` are owned by **root (uid 0)** and **not writable by
  group or other**. A writer who can swap the archive can swap a
  `checksums.txt` beside it, so ownership is checked first.
- The staged archive is then verified against `adguard_home_archive_sha256`, or
  else against a `checksums.txt` in the same directory. The play fails if
  neither is available.
