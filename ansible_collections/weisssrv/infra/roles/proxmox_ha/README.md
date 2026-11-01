# weisssrv.infra.proxmox_ha

Configures Proxmox VE High Availability for VMs and containers: HA rules
(node affinity), HA resources, and ZFS storage replication jobs.

## What it manages

- **HA rules** on Proxmox 9 and later. These are node-affinity rules that
  restrict which nodes may run a guest. Any type other than `node-affinity`
  fails the play.
- **HA resources.** Guests are registered with the HA manager for automatic
  restart and relocation. The role reconciles `state`, `max_restart`,
  `max_relocate` and `comment`, and clears a field removed from config.
- **Storage replication.** One `pvesr` job per `<VMID>-<n>` id. A guest can
  have several targets and fail over to any node holding a replica.
- **Cluster migration channel.** The role pins `migration: type=` in
  `datacenter.cfg`. `insecure` sends guest RAM in cleartext over TCP
  60000-60050. PVE reads only the live value, so an experiment stays in effect
  until something resets it.

The quorum gate runs on every host. The rules, resources and migration-channel
reconciles are cluster-wide. The role probes the host group for a reachable
node and runs them once, delegated to that node. A caller is a plain
`hosts: <proxmox group>` play with no `serial: 1`.

Set `proxmox_ha_delegate_host` to pin the node. When no node answers the probe,
the role fails instead of skipping the reconcile. Delegate resolution is its own
entry point, `tasks_from: delegate`, with the result in `_proxmox_ha_delegate`.
`main.yml` first asserts that this host is in `proxmox_ha_host_group`.

`main.yml` does not include replication. Run it in a separate play against the
source nodes with `tasks_from: replication`. Included from `main.yml`, it would
run once per host in the group.

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

A replication entry also takes `paused` (default `false`). `paused: true` keeps
the job and its replica and holds the job disabled with `--disable 1`. Use it to
stop replication for maintenance on the target node. `enabled: false` deletes
the job, and the replica goes with it.

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
    paused: false     # true: keep the job and replica, hold it disabled
```

## Behaviour worth knowing

- **`ha-manager config` takes no resource argument.** It prints the whole
  index (a `<type>:<vmid>` line plus that resource's indented properties), so
  the role reads it once per run and splits it per SID. A per-resource
  `ha-manager config <sid>` call is rejected by PVE.
- **A job's identity is `(VMID, target)`, not its id.** Proxmox permutes the
  id-to-target pairing when a guest migrates. Every pass matches live jobs to
  entries by target:
  - A live job whose target no enabled entry wants is deleted, but only when
    its id belongs to an inventory entry. Any other such job is an orphan.
  - A live job whose target is still wanted is kept, whatever id it carries.
    Recreating it would force a full ZFS resync.
  - A wanted target with no live job is created. It takes the entry's id when
    that id is free, otherwise the lowest free `<VMID>-<n>`.
  - A recreate never reuses an id pending removal. `pvesr delete` only marks a
    job for removal, so every id in the run's starting index counts as taken.
  - An entry with `enabled: false` deletes the live job serving its target,
    not the job that carries its id.
- **Permutations are reported, not corrected.** The play prints
  `ID PERMUTATION on <host> (id -> live target -> inventory target): ...` and
  sets the `proxmox_ha_permuted_jobs` fact, a list of
  `{id, live_target, inventory_target}`. Both are informational. Do not edit
  the inventory to chase them.
- **`schedule` and `comment` come from the entry for the job's target.** A
  comment therefore never names a node the job does not replicate to. Write each
  entry's `comment` about its own `target_node`.
- **The entry's `paused` decides whether a job is disabled.** A job disabled
  by hand under an entry without `paused: true` is re-enabled with
  `--disable 0`. The play prints `RE-ENABLE on <host>: jobs ...` before the
  update and sets the `proxmox_ha_reenabled_job_ids` fact. Set `paused: true`
  to keep a job disabled across runs.
- **Drift deletes run only while the guest is local**, so a job that could not
  be recreated here is never removed by a drift pass.
- **The live index is read from the API, not `pvesr`.** `pvesr list` has no
  `--output-format`, so the role reads
  `pvesh get /cluster/replication --output-format json`. A failed read aborts
  the reconcile, and so does a read that prints nothing. A cluster with no
  jobs answers `[]`. An empty index would otherwise plan a create for every
  job.
- **A failed local-guest read aborts too.** The role reads `qm list` and
  `pct list` to learn which guests run here. A failed read would look like an
  empty node and skip every create and update. An empty node still passes.
- **Only a concurrent run's same-id create is tolerated.** PVE's
  `replication job '<id>' already exists` passes. A create blocked by another
  job to the same target fails the play, because the live index missed a job.
- **Orphans are reported, never deleted.** This holds for replication jobs, HA
  rules and HA resources. An incomplete config would otherwise destroy state
  that is not codified yet. Clean one up by hand once nothing needs it:
  `pvesr delete <job_id>`, `ha-manager rules remove <rule>` or
  `ha-manager remove <sid>`.
- **A job sourced on another node while the guest is here is kept.** The run
  prints `STALE SOURCE` and names the job. The PVE runner already runs every
  job from the node its guest is on. Deleting the job would purge the replica
  on its target and force a full resync.
- **A guest that left its configured source node is reported only.** The run
  prints `SOURCE DRIFT` and names the jobs. Point the entry's `source_node` at
  the node the guest runs on, or migrate the guest back, then re-run the role.
  `pvesr create-local-job` only works on the node the guest is on.
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
pvesh get /cluster/replication  # the job index this role parses
```

## Molecule

CI runs `molecule test -s default` from the role directory. `ha-manager`,
`pvesh`, `pvesr`, `qm` and `pct` are stubbed and every mutation is logged. Each
case asserts the exact commands issued. The replication cases cover add,
update, re-enable, pause, removal by target, the permuted-target no-op, a
still-wanted target kept under another id, orphan reporting and the
permuted-comment rewrite. The `pvesr` stub keeps a deleted job in the index
and rejects a create of any listed id, as PVE does while a removal is pending.
It can also reject a create, which drives the fail-loud per-target conflict
and the tolerated same-id race. Delegate selection runs in its own play: a
mixed probe group, a non-member host, and a group where nothing answers.
