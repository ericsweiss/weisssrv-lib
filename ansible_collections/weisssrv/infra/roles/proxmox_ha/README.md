# weisssrv.infra.proxmox_ha

Configures Proxmox VE High Availability for VMs and containers: HA rules
(node affinity), HA resources, and ZFS storage replication jobs.

## What it manages

- **HA rules** (Proxmox 9+): node-affinity rules restricting which nodes may
  run a given guest. Only `type: node-affinity` is supported — the role
  asserts this and fails loud on any other type.
- **HA resources**: registers guests with the HA manager (auto restart and
  relocation on failure), reconciling `state`, `max_restart`, `max_relocate`
  and `comment`, including fields *removed* from config.
- **Storage replication**: `pvesr` jobs, one per `<VMID>-<n>` id, with
  multi-target support so a guest can fail over to any node holding a replica.
- **Cluster migration channel** (`datacenter.cfg`): pins `migration: type=` so
  the safe default is declared rather than inherited. `insecure` sends guest RAM
  in cleartext over TCP 60000-60050, and PVE only ever reads the live value, so
  a one-off experiment stays in effect until something puts it back.

The quorum gate runs per host, and the rules, resources and migration-channel
reconciles are cluster-wide: the role probes the host group for a reachable node
and runs them `run_once`, delegated to it. A caller is therefore a plain
`hosts: <proxmox group>` play — no `serial: 1`, no "already applied" bookkeeping.
Set `proxmox_ha_delegate_host` to pin a node instead; when no node answers the
probe, the role fails rather than skipping the reconcile.

Replication is **not** included there — run it in a separate play against the
source nodes with `tasks_from: replication`, or it executes once per host in the
group.

## Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `proxmox_ha_rules` | `[]` | Node-affinity rules (schema below) |
| `proxmox_ha_resources` | `[]` | Guests HA manages (schema below) |
| `proxmox_ha_replication_jobs` | `[]` | `pvesr` replication jobs (schema below) |
| `proxmox_ha_host_group` | `proxmox` | Inventory group whose membership is asserted before touching HA state |
| `proxmox_ha_delegate_host` | unset | Pins the node the cluster-wide reconcile is delegated to; default is the first node that answered the reachability probe |
| `proxmox_ha_migration_type` | `secure` | `datacenter.cfg` migration channel; empty leaves `datacenter.cfg` unmanaged |
| `proxmox_ha_migration_network` | `""` | Dedicated migration network (CIDR); empty preserves the live value |

Every entry supports `enabled` (default `true`); setting it to `false` removes
the rule / resource / job.

```yaml
# One node-affinity rule per service. Nodes may carry explicit priorities: the
# ":2" home wins whenever it is available (fail-back after an outage), ":1"
# entries are ranked fallbacks. A bare node name means priority 0.
proxmox_ha_rules:
  - name: affinity-dns-01
    type: node-affinity
    resources:
      - ct:150
    nodes:
      - "node-a:2"   # home
      - "node-b:1"
      - "node-c:1"
    strict: false     # false: allow an unlisted node if none listed is available
    comment: "dns-01 home node-a"
    enabled: true

proxmox_ha_resources:
  - type: ct          # ct | vm
    vmid: 150
    state: started
    max_restart: 2    # optional: restart attempts on the current node
    max_relocate: 1   # optional: relocation attempts to another node
    comment: "dns-01 (AdGuard Home primary)"
    enabled: true

# Job ids are "<VMID>-<n>"; one entry per target. Prefer explicit staggered
# minute lists over "*/15" so services do not all replicate at once.
proxmox_ha_replication_jobs:
  - id: "150-0"
    source_node: node-a
    target_node: node-b
    schedule: "0,15,30,45"
    comment: "dns-01 -> node-b"
    enabled: true
```

## Behaviour worth knowing

- **`ha-manager config` takes no resource argument.** It prints the whole
  index (a `<type>:<vmid>` line plus that resource's indented properties), so
  the role reads it once per run and splits it per SID. A per-resource
  `ha-manager config <sid>` call is rejected by PVE.
- **Replication drift.** Proxmox permutes job-id↔target pairings when a guest
  migrates. Only a differing target *set* is treated as drift (delete +
  recreate); permuted ids with an equal set are left alone, because churning
  them forces a full ZFS resync per target. A job's `--comment` may therefore
  name a stale target — read the live target from `pvesr list`.
- **Deletes only where they can be repaired.** Jobs are deleted only while the
  guest is local, so a job that could not be recreated here is never removed.
- **Orphans are reported, never deleted** — for replication jobs, HA rules and
  HA resources alike. An incomplete config would otherwise destroy state that is
  simply not codified yet, and a stale node-affinity rule silently constrains
  placement of a resource the role believes it fully controls, so it is at least
  named in the run output. Clean one up by hand with
  `pvesr delete <job_id>` once you have confirmed nothing needs it.
- **Source drift is reported, not corrected**: a guest that migrated away needs
  either the inventory updated or the guest migrated back. The run names the
  jobs; the repair is to point that job's `source_node` at the node the guest
  now runs on, or migrate the guest back, then re-run the role. Replication jobs
  can only be created on the node the guest resides on.
- **PVE9 rule normalization.** `ha-manager rules list --output-format=json`
  returns `nodes` and `resources` as hashes, not comma strings. Both sides are
  normalized to a sorted canonical string before comparison, or the rule is
  re-`set` on every run. A `rules set` only updates the options it is given, so
  a removed comment is cleared with an explicit empty `--comment`.
- **The migration property string is replaced wholesale.** An empty
  `proxmox_ha_migration_network` therefore carries the live network through the
  set rather than clearing it.

## Requirements

- A play-level `become: true` — every `pvesh` / `ha-manager` / `pvesr` call in
  this role runs as root.
- Quorate Proxmox VE cluster (rules require PVE 9+); run from any member.
- Replication targets need matching ZFS storage on every target node.

## Troubleshooting

```bash
ha-manager status          # HA state of every resource
ha-manager rules list      # current node-affinity rules
ha-manager config          # the resource index this role parses
ha-manager migrate ct:150 <node>
pvesr status               # replication job health
```

## Molecule

`molecule test -s default` from the role directory (CI runs the same scenario).
`ha-manager`, `pvesr`, `qm` and `pct` are stubbed and every mutation is logged,
so each case asserts the exact commands issued: add, update, removal
(`enabled: false`), the permuted-target no-op, the unsupported-rule-type
failure, and orphan reporting with zero mutations.
