# weisssrv.infra.proxmox_firewall

Manages the Proxmox VE firewall at cluster, host, and guest level: IPSets for
network groupings, Security Groups for reusable rule sets, and the
`monitoring@pve` user / ACL / API token the Prometheus exporters use.

## What it manages

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
  `sg-metrics`; a node whose `proxmox_role` equals
  `proxmox_firewall_storage_role_name` also gets `sg-nfs-server` and
  `sg-smb-server`), plus anything in `proxmox_firewall_host_extra_groups`
- Optional egress allowlist + trailing `OUT DROP` (see "Egress filtering")
- Inbound drop logging via `proxmox_firewall_log_level_in`
- Which firewall implementation the node runs, via `proxmox_firewall_nftables`
  (see "Firewall implementation")
- Host-specific extra rules (`proxmox_firewall_host_rules`, default empty)

### Guest firewall (`/etc/pve/firewall/<vmid>.fw`)

- `enable: 1` plus one `GROUP <sg>` line per entry in the guest's
  `guest_security_groups`
- Optional `policy_out` (`guest_firewall_policy_out`; `DROP` turns a group's
  `OUT ACCEPT` rules into an enforced egress allowlist)
- Inbound drop logging via `guest_firewall_log_level_in`, defaulting to the
  host-wide `proxmox_firewall_log_level_in`
- Setting `guest_security_groups` to an empty list removes `<vmid>.fw`, returning
  the guest to the unfiltered Proxmox default. Deleting the key entirely leaves
  the file as it is, since the role then treats the guest as unmanaged

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
| `proxmox_firewall_security_groups` | `[]` | Per-application groups (see below). Names are checked against pve-firewall's group grammar and must not repeat a group the role renders itself |
| `proxmox_firewall_storage_role_name` | `nas` | The `proxmox_role` value whose nodes inherit `sg-nfs-server` and `sg-smb-server` in host.fw |
| `proxmox_firewall_host_extra_groups` | `[]` | Extra `GROUP <name>` references appended to this node's host.fw, after the built-ins |
| `proxmox_firewall_dns_admin_ports` | `:443` + `:3000`, admin sets only | Resolver admin surfaces in `sg-dns` (`{port, sources[], comment?}`, asserted) |
| `proxmox_firewall_dns_client_sources` | `[admin_ts, admin_lan]` | IPSets allowed to resolve (`:53` tcp+udp in `sg-dns`) — point at a wider client set once `admin_lan` shrinks to the management subnet |
| `proxmox_firewall_k3s_ingress_int_sources` | `[admin_ts, admin_lan]` | IPSets allowed at the internal Traefik ingress (`:80`/`:443` in `sg-k3s-ingress-int`) — same split |
| `proxmox_firewall_metrics_scrape_ports` | `[]` | Application scrape targets appended to `sg-metrics` (same `{port, sources[], comment?}` schema, asserted) |
| `proxmox_firewall_host_egress_extra_ports` | `[]` | Site services the nodes reach, appended to `sg-host-egress` (`{port, proto, comment?}`, asserted) |
| `proxmox_firewall_smtp_relay_sources` | `[]` | IPSets allowed at the relay's `:25` and `:587` (`sg-smtp-relay`). Empty renders no rule, so the relay stays unreachable until the site names its client sets. The role fails at cluster scope, before `cluster.fw` is written, when an inventory guest carries the group and this is empty |
| `proxmox_firewall_smtp_relay_extra_egress_ports` | `[]` | Extra egress ports the relay guest reaches, appended to `sg-smtp-relay` (`{port, proto, comment?}`) |
| `proxmox_firewall_insecure_migration_ports` | `false` | Open the cleartext live-migration range (60000-60050) |
| `proxmox_firewall_wan_wireguard_vips` | `[]` | VIPs the WAN WireGuard `-dest` rule is scoped to; empty = no such rule |
| `proxmox_firewall_extra_aliases` | `[]` | `[ALIASES]` entries that are not inventory hosts (`{name, cidr, comment?}`) |
| `proxmox_firewall_cluster_rules` | `[]` | Extra raw lines under cluster.fw `[RULES]` |
| `proxmox_firewall_host_rules` | `[]` | Extra raw lines under host.fw `[RULES]`, rendered **before** the security groups and the egress DROP — first-match-wins, so they outrank every group rule |
| `proxmox_firewall_host_group` | `proxmox` | Inventory group holding the nodes |
| `proxmox_firewall_enabled` | `true` | Render and deploy the firewall at all — cluster.fw, host.fw, guest `<vmid>.fw` and the pve-firewall service. The `monitoring@pve` user/token reconcile is governed separately by `proxmox_firewall_skip_pveum` |
| `proxmox_firewall_egress_filtering` | `false` | Host-originated egress default-deny |
| `proxmox_firewall_log_level_in` | `nolog` | host.fw inbound drop logging, and the default for guests (`info` for triage) |
| `proxmox_firewall_nftables` | `false` | Select the nftables firewall implementation (`proxmox-firewall`) on this node instead of the iptables `pve-firewall`. Per node, PVE 9 — see "Firewall implementation" |
| `proxmox_firewall_config_dir` | `/etc/pve/firewall` | pmxcfs firewall dir |
| `proxmox_firewall_node_dir` | `/etc/pve/nodes` | pmxcfs per-node dir |
| `proxmox_firewall_staging_dir` | `/var/lib/pve-firewall-ansible` | Off-pmxcfs render staging |
| `proxmox_firewall_skip_pveum` | `false` | Skip pveum / `pve-firewall` service tasks (containerised test runs) |
| `proxmox_firewall_compile_error_patterns` | `error`, `unable to`, `skip line`, `no such `, `invalid `, `unknown ` | Substrings in `pve-firewall compile` stderr that mean a parse error. Matched case-insensitively, because the command can exit 0 on one |
| `proxmox_firewall_compile_fail_on_line_errors` | `true` | Also treat a `(line N)` complaint in that stderr as a parse error, whatever its wording |
| `proxmox_firewall_compile_failed` | derived from the two above | The shared predicate both compile checks use. Derived, not an input |

Per-host inventory keys the templates read: `firewall_ipsets`,
`firewall_alias`, `firewall_alias_comment`, `guest_security_groups`,
`guest_firewall_policy_out`, `guest_firewall_log_level_in`, `vmid`, `proxmox_role`,
`proxmox_firewall_node_ip` (test override for `ansible_host`); plus
`firewall_ipset_special_entries` for non-host IPSet members, and
`firewall_deploy_host` to pin the Proxmox node that writes this guest's `.fw`
(highest precedence; the default is the first node that answered the
reachability probe).

### Cluster-task delegate

`cluster.fw` and the pveum monitoring user and token are cluster-wide. The role
probes the Proxmox group once and delegates those tasks to the first host that
answered, so one node being down does not fail the whole deploy. When nothing
answered it falls back to the group's first member and says so at run time.
Every host derives the delegate from the probe runner's fact, so the `run_once`
delegation and the per-host `hostvars` reads agree even in guest-only plays.

`run_once` fires once per play, and several plays include this role, so
completion is recorded as a `delegate_facts` fact on the delegate that later
plays read through `hostvars`. That keeps the repeated `pveum ... list` probes
and the second `cluster.fw` render out of every later play.

### Variable scope

Guest plays include this role too, and `cluster.fw` is rendered from the play's
first host, not from the Proxmox delegate. Every cluster-scope input must
therefore be defined at a scope every including play sees (`group_vars/all`),
never in the Proxmox group's vars: `proxmox_firewall_security_groups`,
`_extra_aliases`, `_admin_lan_cidrs`, `_admin_ts_cidrs`, `_smb_client_cidrs`,
`_dns_admin_ports`, `_dns_client_sources`, `_k3s_ingress_int_sources`,
`_metrics_scrape_ports`, `_host_egress_extra_ports`, `_smtp_relay_sources`,
`_smtp_relay_extra_egress_ports`, `_wan_wireguard_vips`,
`_insecure_migration_ports` and `_cluster_rules` (the `sg-host-egress` group
itself lives in cluster.fw; only the flag that references it is per host). The role asserts this against the delegate's own scope and
fails loudly, naming the variable.

Only host-scope inputs belong in the Proxmox group's vars:
`proxmox_firewall_egress_filtering`, `_host_rules`, `_log_level_in`,
`_storage_role_name`, `_host_extra_groups` and `proxmox_role`.

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
# `ip` is required (address or CIDR), `comment` is optional.
firewall_ipset_special_entries:
  k3s_nodes:
    - ip: 10.0.0.161
      comment: k3s API VIP

proxmox_firewall_extra_aliases:
  - name: api-vip
    cidr: 10.0.0.161
    comment: k3s API VIP
```

### Security groups

The role owns the **infrastructure** groups in `templates/cluster.fw.j2`:
`sg-dns`, `sg-host-admin`, `sg-vm-admin`, `sg-k3s-core`, `sg-k3s-ingress-int`,
`sg-k3s-ingress-pub`, `sg-nfs-server`, `sg-metrics`, `sg-pve-cluster`,
`sg-smb-server`, `sg-smtp-relay`, `sg-host-egress`. Any other application group
is site data in `proxmox_firewall_security_groups`.

`sg-nfs-server` and `sg-smb-server` are referenced by the nodes whose
`proxmox_role` equals `proxmox_firewall_storage_role_name`, so a storage node
tagged something other than the default `nas` sets that variable instead of
forking the template.

A group nothing references is inert, so `sg-smtp-relay` costs a site with no
mail relay nothing. Its `OUT` rules only bite on a guest running
`guest_firewall_policy_out: DROP`.

Three of those carry typed seams instead of hard-coded application ports,
because `host.fw` applies `sg-metrics` on every node, the resolver admin API is
a credentialed surface, and a relay's log sink is site data:

- `sg-metrics` builds in this collection's own exporter defaults (9101, 9134,
  9167) plus the in-cluster node-exporter's 9100. An app's scrape port is site
  data:
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
      - {ip: 10.10.20.0/24, comment: home}
      - {ip: 10.10.30.0/24, comment: iot}
    lan_clients:                       # trusted user VLANs reach the apps
      - {ip: 10.10.20.0/24, comment: home}

  proxmox_firewall_admin_lan_cidrs: ["10.0.1.0/24"]   # management only
  proxmox_firewall_dns_client_sources: [admin_ts, dns_clients]
  proxmox_firewall_k3s_ingress_int_sources: [admin_ts, lan_clients]
  ```

  Each name renders one `+dc/<name>` rule per port, in list order. The resolver
  *admin* surfaces stay on `proxmox_firewall_dns_admin_ports` — widening the
  client scope must not widen the credentialed API with it. Every name must be
  a set the site actually declares: a `+dc/` naming an undeclared IPSet makes
  pve-firewall refuse the whole datacenter ruleset after the role has already
  written the file. pve-firewall picks up `/etc/pve` changes on its own, so the
  refusal shows up only in `pve-firewall compile` or the service log — which is
  why the role runs that compile itself after every changed publish.
- All four hand-authored lists share one schema, asserted at role entry. On the
  port lists `port` is required and `sources` must be a non-empty **list**; the
  two client-scope lists must themselves be non-empty lists. A bare scalar
  (`dns_client_sources: dns_clients`) is rejected — the template iterates it per
  character, emitting rules that name IPSets which do not exist — and so is an
  empty list, which renders a cluster.fw that compiles perfectly with the
  surface it scopes closed to everyone.

**Per-application** groups are site data in `proxmox_firewall_security_groups`,
empty by default so an unconfigured deployment renders none. Each entry is
`{name, rules}`; `rules` is a list of raw cluster.fw lines — comments included —
emitted verbatim and in order (pve-firewall is first-match-wins within a group).
Reference a group from a guest through its `guest_security_groups` list.

Names are checked at role entry. Each one must match pve-firewall's own group
grammar: a leading letter or digit, then letters, digits, `_` or `-`, 2 to 18
characters. The twelve names above are reserved, and two entries may not share a
name. A repeat renders a second `[group <name>]` section in cluster.fw, where
pve-firewall keeps one rule set of the two and reports nothing — so either the
site's app rules or a built-in allow disappears. The reserved list lives in the
role's `vars/main.yml`.

A node can also reference extra groups directly:

```yaml
# host_vars — appended to this node's host.fw after the built-in references.
proxmox_firewall_host_extra_groups:
  - sg-myapp
```

The group itself must exist in cluster.fw. These references land after the
built-ins, so on a node running egress filtering the trailing `OUT DROP`
outranks any `OUT ACCEPT` they carry.

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

A changed publish runs `pve-firewall compile` on the writing node. The role also
runs one unconditional `pve-firewall compile` at the end, so a ruleset that is
already live and uncompilable fails every run instead of only the run that
introduced it.

## Dependencies

None — foundational role.

## Firewall implementation

Proxmox VE 9 ships two implementations of the same rule files. The classic
`pve-firewall` programs iptables and filters bridged guest traffic through the
kernel's bridge-netfilter hook. The `proxmox-firewall` package programs nftables
instead and takes that hook out of the node's bridged path, which is the way out
of a kernel bug on that path.

`proxmox_firewall_nftables: true` renders `nftables: 1` into the node's
`host.fw`, and that option is what selects the nftables implementation. The
choice is per node, so set it in `host_vars` for the nodes you are switching.
False omits the option, so a node switched by hand reverts on the next converge.

Install the `proxmox-firewall` package on the node first. This role installs no
packages, and a node that selects nftables with nothing to program the rules can
come up unfiltered.

Switch one node and check it before the rest: `pve-firewall status`, the node
still reachable, its guests still reaching the network, and the rules you depend
on still applying to both.

## Egress filtering

Inbound is default-deny; host-originated **egress** is `ACCEPT` unless
`proxmox_firewall_egress_filtering` is true. When enabled, `host.fw` references
the `sg-host-egress` allowlist (DNS/NTP/HTTP(S)/Tailscale/corosync/SSH/NFS/SMTP/
migration) and appends a trailing `OUT DROP` rule. `pve-firewall` honours OUT
*rules* in `host.fw` but **ignores** the host-level `policy_out` option (that key
is only effective in `cluster.fw`), so the trailing `OUT DROP` — not a policy
setting — is what enforces default-deny. Guests are unaffected by host rules;
they opt in separately with `guest_firewall_policy_out: DROP`.

Site services on other ports go through the seam rather than a fork of the
template:

```yaml
proxmox_firewall_host_egress_extra_ports:
  - {port: 2222, proto: tcp, comment: forge SSH}
  - {port: 31100, proto: tcp, comment: log sink NodePort}
```

Rolling it out — a missing allowlist entry can cut a node off:

1. Enable it on one non-critical node first, deploy, then check
   `pve-firewall compile`, that the node stays reachable, that it still shows in
   `pvecm status`, and that it can reach apt and the overlay network.
2. The `OUT DROP` rule logs at `info`: review the kernel log for drops and extend
   `sg-host-egress` for any legitimate egress that was missed.
3. Once stable, enable it for the rest of the nodes in `group_vars`.

## Testing

Every changed publish runs `pve-firewall compile` on the node that wrote the
file and fails the play when it refuses the ruleset, so a bad rule stops the run
instead of sitting unnoticed. Ad-hoc checks:

```bash
pve-firewall status          # service + compiled ruleset state
pve-firewall compile         # validate cluster.fw/host.fw before relying on them
pvesh get /cluster/firewall/ipset
pvesh get /cluster/firewall/groups
```

From a host inside `admin_lan` / `admin_ts`, SSH and `:8006` must succeed; from
anywhere else they must not.

## Molecule

`molecule test -s default` from the role directory (CI runs the same scenario).
It renders cluster.fw, host.fw and the guest files into a stub directory and
asserts the rule lines section by section, the schema guards failing closed
(reserved, duplicate and ungrammatical group names included), the
egress-filtering branch, the nftables option, the storage-role seam and its
extra group references, the cross-play run-once guard and that every `+dc/`
reference resolves to a declared IPSet.
