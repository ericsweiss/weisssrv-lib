# weisssrv.infra.proxmox_ha

Configures Proxmox VE High Availability for VMs and containers: HA rules
(node affinity), HA resources, and ZFS storage replication jobs.

## What This Role Manages

- **HA rules** (Proxmox 9+): node-affinity rules restricting which nodes may
  run a given guest. Only `type: node-affinity` is supported — the role
  asserts this and fails loud on any other type.
- **HA resources**: registers guests with the HA manager (auto restart and
  relocation on failure), reconciling `state`, `max_restart`, `max_relocate`
  and `comment`, including fields *removed* from config.
- **Storage replication**: `pvesr` jobs, one per `<VMID>-<n>` id, with
  multi-target support so a guest can fail over to any node holding a replica.

`tasks/main.yml` runs the rules + resources reconciliation. Replication is
**not** included there — run it in a separate play against the source nodes
with `tasks_from: replication`, or it executes once per host in the group.

## Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `proxmox_ha_rules` | `[]` | Node-affinity rules (schema below) |
| `proxmox_ha_resources` | `[]` | Guests HA manages (schema below) |
| `proxmox_ha_replication_jobs` | `[]` | `pvesr` replication jobs (schema below) |
| `proxmox_ha_host_group` | `proxmox` | Inventory group whose membership is asserted before touching HA state |

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
- **Orphaned jobs are reported, never deleted** — an incomplete config would
  otherwise destroy jobs that are simply not codified yet.
- **Source drift is reported, not corrected**: a guest that migrated away
  needs either the inventory updated or the guest migrated back.

## Requirements

- Quorate Proxmox VE cluster (rules require PVE 9+); run from any member.
- Replication targets need matching ZFS storage on every target node.

## Files

- `tasks/main.yml` — cluster/quorum gate, then rules + resources
- `tasks/rules.yml` — node-affinity rules
- `tasks/resources.yml` — HA resources
- `tasks/replication.yml` — `pvesr` replication jobs (`tasks_from: replication`)

## Troubleshooting

```bash
ha-manager status          # HA state of every resource
ha-manager rules list      # current node-affinity rules
ha-manager config          # the resource index this role parses
ha-manager migrate ct:150 <node>
pvesr status               # replication job health
```
