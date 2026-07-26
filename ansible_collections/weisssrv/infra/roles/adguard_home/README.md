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
- Reconciliation: adds missing, deletes orphaned

### Via file edit (upstream limitation)

The admin password hash is reconciled by rewriting `AdGuardHome.yaml`: AdGuard
Home has no password API (`/control/profile/update` takes only
name/language/theme). The role **parses** the document, replaces
`users[?name == adguard_home_admin_user].password` and writes the whole file
back — a line-oriented edit would target "the last password line", i.e. a
different account's the moment a second user exists. The rewrite is gated on the
existing hash failing verification, so a converged host is untouched.

### Not managed

- HTTP/DNS port changes after setup (they need a service restart)
- Filter lists (managed in the UI)

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
adguard_home_tls_server_name: "dns.{{ internal_domain }}"   # must match the cert
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

# DHCP
adguard_home_dhcp_enabled: false

# DNS rewrites (managed via API)
adguard_home_rewrites:
  - domain: "example.{{ internal_domain }}"
    answer: "10.0.0.x"

# Custom filtering rules (managed via API)
adguard_home_user_rules:
  - '||192.0.168.192.in-addr.arpa^$dnsrewrite=NOERROR;PTR;example.{{ internal_domain }}.'
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

- `tasks/main.yml` - Main task orchestration
- `tasks/api_base_config.yml` - Base settings management via API
- `tasks/api_config.yml` - DNS records management via API
- `templates/adguardhome.service.j2` - Systemd service
- `handlers/main.yml` - Service restart handler

## Dependencies

- `weisssrv.infra.unbound` — the default upstream resolver on 127.0.0.1:5335
- `weisssrv.infra.acme_certs` — distributes the TLS material
- `weisssrv.infra.adguard_sync` — replicates primary → replica

## Security

- All API calls use HTTP Basic Auth with the configured admin credentials
- API calls use `no_log: true` to prevent credential exposure
- Runs as unprivileged `adguard` user with `CAP_NET_BIND_SERVICE`
- Config file owned by `adguard:adguard` with mode `0600`
