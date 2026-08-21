# proxmox_firewall

Manages the Proxmox VE firewall at cluster, host, and guest level: IPSets for
network groupings, Security Groups for reusable rule sets, and the
`monitoring@pve` user / ACL / API token the Prometheus exporters use.

## What this role manages

### Cluster firewall (`/etc/pve/firewall/cluster.fw`)

- Global options (`policy_in: DROP`, `policy_out: ACCEPT`)
- IPSets: `admin_lan`, `admin_ts` and `smb_clients` come from the
  `proxmox_firewall_*_cidrs` variables; every other set (`core-cluster`,
  `k3s_nodes`, `pve_hosts`, `nfs_clients`, …) is rendered from inventory
- `[ALIASES]`: derived from inventory (a host sets `firewall_alias`, optionally
  `firewall_alias_comment`), plus `proxmox_firewall_extra_aliases` for addresses
  that are not inventory hosts. An extra with the same name wins
- Infrastructure security groups, defined in `templates/cluster.fw.j2`
- Per-application security groups from `proxmox_firewall_security_groups`
- Cluster-wide rules (`proxmox_firewall_cluster_rules`, default empty)

### Host firewall (`/etc/pve/nodes/<node>/host.fw`)

- Per-host enable + base group references (`sg-pve-cluster`, `sg-host-admin`,
  `sg-metrics`; a node with `proxmox_role: nas` also gets `sg-nfs-server` and
  `sg-smb-server`)
- Optional egress allowlist + trailing `OUT DROP` (see "Egress filtering")
- Inbound drop logging via `proxmox_firewall_log_level_in`
- Host-specific extra rules (`proxmox_firewall_host_rules`, default empty)

### Guest firewall (`/etc/pve/firewall/<vmid>.fw`)

- `enable: 1` plus one `GROUP <sg>` line per entry in the guest's
  `guest_security_groups`
- Optional `policy_out` (`guest_firewall_policy_out`; `DROP` turns a group's
  `OUT ACCEPT` rules into an enforced egress allowlist)
- Inbound drop logging via `guest_firewall_log_level_in`, defaulting to the
  host-wide `proxmox_firewall_log_level_in`

### Monitoring user and API token (pveum)

Creates `monitoring@pve`, grants `PVEAuditor` at `/`, and creates the
`monitoring@pve!exporter` token (`--privsep 0`) — once per invocation,
cluster-wide. The secret is printed only at creation; the role discards it
(`no_log`) and prints how to recover it (`/etc/pve/priv/token.cfg`) or rotate it
into the secret store the exporters read.

## Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `proxmox_firewall_admin_lan_cidrs` | **required** | Management-plane sources (`:22`, `:8006`, `:6443`) — no default, an empty set would lock every node out |
| `proxmox_firewall_admin_ts_cidrs` | `[100.64.0.0/10]` | Admin overlay sources (Tailscale CGNAT range) |
| `proxmox_firewall_smb_client_cidrs` | admin LAN | Sources allowed SMB (`sg-smb-server`) |
| `proxmox_firewall_security_groups` | `[]` | Per-application groups (see below) |
| `proxmox_firewall_dns_admin_ports` | `:443` + `:3000`, admin sets only | Resolver admin surfaces in `sg-dns` (`{port, sources[], comment?}`, asserted) |
| `proxmox_firewall_dns_client_sources` | `[admin_ts, admin_lan]` | IPSets allowed to resolve (`:53` tcp+udp in `sg-dns`) — point at a wider client set once `admin_lan` shrinks to the management subnet |
| `proxmox_firewall_k3s_ingress_int_sources` | `[admin_ts, admin_lan]` | IPSets allowed at the internal Traefik ingress (`:80`/`:443` in `sg-k3s-ingress-int`) — same split |
| `proxmox_firewall_metrics_scrape_ports` | `[]` | Application scrape targets appended to `sg-metrics` (same `{port, sources[], comment?}` schema, asserted) |
| `proxmox_firewall_insecure_migration_ports` | `false` | Open the cleartext live-migration range (60000-60050) |
| `proxmox_firewall_wan_wireguard_vips` | `[]` | VIPs the WAN WireGuard `-dest` rule is scoped to; empty = no such rule |
| `proxmox_firewall_extra_aliases` | `[]` | `[ALIASES]` entries that are not inventory hosts (`{name, cidr, comment?}`) |
| `proxmox_firewall_cluster_rules` | `[]` | Extra raw lines under cluster.fw `[RULES]` |
| `proxmox_firewall_host_rules` | `[]` | Extra raw lines under host.fw `[RULES]`, rendered **before** the security groups and the egress DROP — first-match-wins, so they outrank every group rule |
| `proxmox_firewall_host_group` | `proxmox` | Inventory group holding the nodes |
| `proxmox_firewall_enabled` | `true` | Render and deploy at all |
| `proxmox_firewall_egress_filtering` | `false` | Host-originated egress default-deny |
| `proxmox_firewall_log_level_in` | `nolog` | host.fw inbound drop logging, and the default for guests (`info` for triage) |
| `proxmox_firewall_config_dir` | `/etc/pve/firewall` | pmxcfs firewall dir |
| `proxmox_firewall_node_dir` | `/etc/pve/nodes` | pmxcfs per-node dir |
| `proxmox_firewall_staging_dir` | `/var/lib/pve-firewall-ansible` | Off-pmxcfs render staging |
| `proxmox_firewall_skip_pveum` | `false` | Skip pveum / `pve-firewall` service tasks (containerised test runs) |

Per-host inventory keys the templates read: `firewall_ipsets`,
`firewall_alias`, `firewall_alias_comment`, `guest_security_groups`,
`guest_firewall_policy_out`, `guest_firewall_log_level_in`, `vmid`, `proxmox_role`,
`proxmox_firewall_node_ip` (test override for `ansible_host`); plus
`firewall_ipset_special_entries` for non-host IPSet members.

## Configuration

IPSet membership is declared per host, not as a central map:

```yaml
# hosts.yml — each named IPSet gains this host's address.
dns-01:
  firewall_ipsets:
    - core-cluster
  firewall_alias: dns-01
  firewall_alias_comment: Primary DNS server
  guest_security_groups:      # rendered into /etc/pve/firewall/<vmid>.fw
    - sg-vm-admin
    - sg-dns

# group_vars — members that are not inventory hosts (VIPs, off-cluster peers).
firewall_ipset_special_entries:
  k3s_nodes:
    - ip: 192.168.0.161
      comment: k3s API VIP

proxmox_firewall_extra_aliases:
  - name: api-vip
    cidr: 192.168.0.161
    comment: k3s API VIP
```

### Security groups

The role owns the **infrastructure** groups in `templates/cluster.fw.j2`:
`sg-dns`, `sg-host-admin`, `sg-vm-admin`, `sg-k3s-core`, `sg-k3s-ingress-int`,
`sg-k3s-ingress-pub`, `sg-nfs-server`, `sg-metrics`, `sg-pve-cluster`,
`sg-smb-server`, `sg-smtp-relay`, `sg-host-egress`.

Two of those carry typed seams instead of hard-coded application ports, because
`host.fw` applies `sg-metrics` on every node and the resolver admin API is a
credentialed surface:

- `sg-metrics` builds in only the exporters this collection's roles bind
  (9100, 9101, 9134, 9167). An app's scrape port is site data:
  `proxmox_firewall_metrics_scrape_ports: [{port: 32400, sources: [k3s_nodes], comment: plex}]`.

  Each entry's `sources` are IP-set names, so a port whose scraper is not the
  k3s nodes names its own set:

  ```yaml
  proxmox_firewall_metrics_scrape_ports:
    - {port: 32400, sources: [k3s_nodes], comment: plex}
    - {port: 31100, sources: [core-cluster], comment: loki push NodePort}
  ```

  Repos upgrading from the release where the template built these ports in
  find the removed set in [MIGRATING.md](../../MIGRATING.md).
- `sg-dns` builds in 53/853 and takes its admin surfaces from
  `proxmox_firewall_dns_admin_ports`, which defaults to the two admin sets on
  :443 and :3000. Admitting a scraper to the plaintext :3000 API means adding
  its set to that entry's `sources` — that API answers HTTP Basic in the clear.
- The two **service** surfaces — resolution (`:53` in `sg-dns`) and the internal
  ingress (`:80`/`:443` in `sg-k3s-ingress-int`) — take their client scope from
  `proxmox_firewall_dns_client_sources` and
  `proxmox_firewall_k3s_ingress_int_sources`. Both default to
  `[admin_ts, admin_lan]`, so a site that leaves them alone renders what it
  always did. On a segmented network they are what lets `admin_lan` shrink to
  the management subnet without taking DNS or the apps down with it: declare the
  client subnets as their own IPSets and name them here.

  ```yaml
  firewall_ipset_special_entries:
    dns_clients:                       # every VLAN needs resolution
      - {ip: 10.0.20.0/24, comment: home}
      - {ip: 10.0.30.0/24, comment: iot}
    lan_clients:                       # trusted user VLANs reach the apps
      - {ip: 10.0.20.0/24, comment: home}

  proxmox_firewall_admin_lan_cidrs: ["10.0.1.0/24"]   # management only
  proxmox_firewall_dns_client_sources: [admin_ts, dns_clients]
  proxmox_firewall_k3s_ingress_int_sources: [admin_ts, lan_clients]
  ```

  Each name renders one `+dc/<name>` rule per port, in list order. The resolver
  *admin* surfaces stay on `proxmox_firewall_dns_admin_ports` — widening the
  client scope must not widen the credentialed API with it.
- Both port lists share one schema, asserted at role entry: `port` is required and
  `sources` must be a non-empty **list**. A bare scalar (`sources: k3s_nodes`)
  is rejected — the template iterates it per character, emitting rules that name
  IPSets which do not exist.

**Per-application** groups are site data in `proxmox_firewall_security_groups`,
empty by default so an unconfigured deployment renders none. Each entry is
`{name, rules}`; `rules` is a list of raw cluster.fw lines — comments included —
emitted verbatim and in order (pve-firewall is first-match-wins within a group).
Reference a group from a guest through its `guest_security_groups` list.

Worked example:

```yaml
proxmox_firewall_security_groups:
  # Ordinary web app behind the cluster ingress.
  - name: sg-myapp
    rules:
      - "# ingress -> app TLS, plus admin sources for direct debugging"
      - "IN ACCEPT -source +dc/k3s_nodes -p tcp -dport 443 -log nolog"
      - "IN ACCEPT -source +dc/admin_ts -p tcp -dport 443 -log nolog"
      - "IN ACCEPT -source +dc/admin_lan -p tcp -dport 443 -log nolog"
      - "# app-native Prometheus telemetry, scraped from the cluster"
      - "IN ACCEPT -source +dc/k3s_nodes -p tcp -dport 9205 -log nolog"

  # A port open to the WAN — state the compensating control in the rules
  # themselves, since a reader of cluster.fw sees only these lines.
  - name: sg-myapp-public
    rules:
      - "# :2222 is reachable from the WAN by design; the service authenticates"
      - "# every session and fail2ban bans repeated failures."
      - "IN ACCEPT -p tcp -dport 2222 -log nolog"

  # Group whose members come from a variable: `rules` may be an expression, but
  # it MUST evaluate to a list — a string iterates character-by-character.
  - name: sg-inference
    rules: >-
      {{ myapp_inference_clients
         | map('regex_replace', '^', 'IN ACCEPT -source ')
         | map('regex_replace', '$', ' -p tcp -dport 3003 -log nolog')
         | list }}
```

An authless port is a case where the group *is* the security boundary: admit
only the specific consumers, and let an empty list admit nothing.

The cleartext live-migration range (TCP 60000-60050) is **not** opened. PVE's
default migration channel is the SSH tunnel, `weisssrv.infra.proxmox_ha` pins
`migration: type=secure` in `datacenter.cfg`, and pre-authorising the range
would make a flip to `insecure` — guest RAM on the wire in the clear — invisible
at the packet filter. Set `proxmox_firewall_insecure_migration_ports: true` only
alongside a deliberate `proxmox_ha_migration_type: insecure`.

## Architecture

```
Proxmox cluster firewall
├─ /etc/pve/firewall/cluster.fw          IPSets, aliases, security groups
├─ /etc/pve/nodes/<node>/host.fw         per-host rules and group refs
└─ /etc/pve/firewall/<vmid>.fw           per-guest group refs
```

## Files

- `tasks/main.yml` — orchestration (firewall configs + pveum monitoring user/token)
- `tasks/guest.yml` — per-guest deployment (included when `vmid` is set)
- `tasks/probe-delegate.yml` — single reachability probe used to pick the delegate
- `tasks/deploy-pmxcfs-config.yml` — pmxcfs-safe publisher shared by all three
  config levels (see below)
- `templates/cluster.fw.j2`, `host.fw.j2`, `guest.fw.j2`, `ipsets.j2`

### Writing into pmxcfs

`/etc/pve` is the Proxmox clustered FUSE filesystem. It enforces
`root:www-data 0640` on the firewall files and **rejects every explicit
`chown` / `chmod` / `utime` with `EPERM`**. Ansible's `template`/`copy` land
content through `atomic_move`, whose fallback runs `shutil.copy2` (calling
`utime`) and then re-applies owner/group/mode — so a create or content change
false-fails with "Operation not permitted" even though the bytes were written,
and `unsafe_writes` does not route around it on ansible-core 2.20+.
`tasks/deploy-pmxcfs-config.yml` therefore renders each config to
`proxmox_firewall_staging_dir` on the node's normal root filesystem and
publishes it with a plain `cp` — no metadata syscalls — only when the live
content differs.

## Dependencies

None — foundational role.

## Egress filtering

Inbound is default-deny; host-originated **egress** is `ACCEPT` unless
`proxmox_firewall_egress_filtering` is true. When enabled, `host.fw` references
the `sg-host-egress` allowlist (DNS/NTP/HTTP(S)/Tailscale/corosync/SSH/NFS/SMTP/
migration) and appends a trailing `OUT DROP` rule. `pve-firewall` honours OUT
*rules* in `host.fw` but **ignores** the host-level `policy_out` option (that key
is only effective in `cluster.fw`), so the trailing `OUT DROP` — not a policy
setting — is what enforces default-deny. Guests are unaffected by host rules;
they opt in separately with `guest_firewall_policy_out: DROP`.

Rolling it out — a missing allowlist entry can cut a node off:

1. Enable it on one non-critical node first, deploy, then check
   `pve-firewall compile`, that the node stays reachable, that it still shows in
   `pvecm status`, and that it can reach apt and the overlay network.
2. The `OUT DROP` rule logs at `info`: review the kernel log for drops and extend
   `sg-host-egress` for any legitimate egress that was missed.
3. Once stable, enable it for the rest of the nodes in `group_vars`.

## Testing

```bash
pve-firewall status          # service + compiled ruleset state
pve-firewall compile         # validate cluster.fw/host.fw before relying on them
pvesh get /cluster/firewall/ipset
pvesh get /cluster/firewall/groups
```

From a host inside `admin_lan` / `admin_ts`, SSH and `:8006` must succeed; from
anywhere else they must not.
