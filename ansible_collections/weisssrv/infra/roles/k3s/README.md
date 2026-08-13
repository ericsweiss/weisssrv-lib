# weisssrv.infra.k3s

Installs and configures a k3s cluster: embedded etcd, a kube-vip API VIP,
passthrough-disk persistent storage, and node labels/taints for workload
placement.

## What This Role Manages

### Prerequisites
- Package installation (curl, open-iscsi, nfs-common)
- iscsid service enablement
- Config directory creation
- Kernel inotify ceilings (`/etc/sysctl.d/90-k3s-inotify.conf`): raises
  `fs.inotify.max_user_instances` (128 → 8192) and `max_user_watches` so a
  container-dense node never exhausts the host-global, per-UID inotify-instance
  pool. Without it, a fresh molecule CI container's systemd PID 1 intermittently
  dies at boot with *"Failed to allocate manager object: Too many open files"*
  and the job fails at molecule's *"Wait for systemd to be ready"* prepare step.
  Toggle with `k3s_inotify_tuning`.

### Persistent Storage
- Additional disk formatting (passthrough block devices, e.g. ZFS zvols)
- Filesystem creation (ext4)
- UUID-based mounting in /etc/fstab
- Mount point creation and management

### K3s Installation
- Version checking and upgrading (pinned installer script, optional sha256 pin
  via `k3s_install_script_checksum`)
- Server installation (embedded etcd, `secrets-encryption: true`, WireGuard
  flannel backend)
- Agent installation (connects to the API VIP with the lower-privilege agent
  token; existing agents are migrated off the server token)
- Kube-vip manifest deployment (first server only), pinned
  `system-node-critical` with a memory limit so the pod owning the API VIP is
  neither preemptible nor BestEffort
- metrics-server override (first server only, opt-in — see below)
- /etc/hosts pins: container-registry hostname → internal Traefik VIP
  (`k3s_registry_host_pins`) and NAS storage hostname for NFS-over-TLS PVs
  (`k3s_storage_host_pins`)
- Node label application
- Node taint application
- kube-apiserver audit logging (opt-in, servers only) — see below
- Off-node etcd snapshot copy (opt-in, servers only): a systemd timer that
  copies the newest local etcd snapshot to an NFS export (by hostname, over
  TLS) and emits an `etcd_snapshot_last_copy_timestamp_seconds`
  textfile metric for the `EtcdSnapshotStale` alert — off by default via
  `k3s_etcd_snapshot_offnode_enabled` (see defaults for the companion NFS export
  + `node_exporter_host` + `nfs_tls`/tlshd on the servers it needs — the
  `xprtsec=tls` mount hangs without the TLS handshake daemon)

Each of those three opt-in features **converges on opt-out**: setting the flag
back to `false` stops and disables the snapshot timer, drops its NFS mount and
removes its units and script; removes the metrics-server override from the
manifests dir (k3s auto-applies whatever is left there); and removes the audit
policy file. Otherwise a flag flip would leave the feature running.

## Required variables

The role ships **no** version pins and **no** site addresses: a role-local
duplicate of a site's pin silently deploys the stale value the day the two
drift. These are asserted, so a dropped inventory var fails the run.

| Variable | Aliases | Required on | Notes |
|---|---|---|---|
| `k3s_token` | — | all nodes | server/cluster join token; a secret |
| `k3s_version` | — | all nodes | e.g. `v1.36.3+k3s1` |
| `k3s_api_vip` | — | all nodes | the kube-vip API address |
| `k3s_kube_vip_version` | `kube_vip_version` | servers | kube-vip image tag |
| `k3s_gpu_*` (4 pins) | `nvidia_*` | GPU agents | see the GPU section |

Everything else has a working default. The ones a site almost always sets:
`k3s_agent_token` (falls back to `k3s_token`, which is fine for a test run and
wrong for production — an agent token cannot promote a node),
`k3s_kube_vip_interface` (alias `kube_vip_interface`, default `eth0`) — the
kube-vip DaemonSet is rendered once, on the first server, and runs on all of
them, so every server must name the SAME interface; the role asserts both that
the NIC exists per host and that the servers agree — the agreement check runs on
EVERY server, because role defaults are absent from `hostvars` and the first
server alone cannot see that its peers resolved a different default,
`k3s_registry_host_pins` / `k3s_storage_host_pins` (both `[]`),
`k3s_etcd_snapshot_nfs_server` (empty; required once the off-node copy is on),
`k3s_disable`, `k3s_labels`, `k3s_taints`.

The agreement check also asserts the host is a MEMBER of `k3s_server_group`,
because a misnamed group resolves to an empty list and an empty set trivially
agrees with itself — that clause is what stops the assert passing vacuously.
**`--limit` caveat:** under `ansible-playbook --limit <one-server>` only that
host evaluates the set, so the agreement half is only as wide as the limit; run
the control-plane nodes together before trusting it.

## Configuration

```yaml
# Cluster
k3s_api_vip: "10.0.0.161"          # required — the kube-vip API address
k3s_token: "<server/cluster join token>"
k3s_agent_token: "<lower-privilege agent join token>"
k3s_version: "v1.36.3+k3s1"
k3s_kube_vip_version: "v1.2.2"     # required on servers
k3s_server_group: k3s_servers      # inventory group holding the servers

# Extra apiserver-certificate SANs, on top of k3s_api_vip, inventory_hostname
# and ansible_host. Defaults to ["k3s.<k3s_internal_domain>"], and
# k3s_internal_domain defaults to the inventory-wide `internal_domain` (unset =
# no extra SAN). Changing this needs the existing serving cert removed so k3s
# regenerates it.
k3s_internal_domain: example.com
k3s_tls_sans: ["k3s.example.com"]

# Server-specific
k3s_role: server
k3s_is_first_server: true          # exactly one

# Agent-specific
k3s_role: agent

# Node customization — the label namespace is the site's, not the library's
k3s_labels:
  example.com/nas: "true"
  example.com/general: "true"
k3s_taints:
  - key: example.com/nas
    value: "true"
    effect: PreferNoSchedule

# /etc/hosts pins (both default to [])
k3s_registry_host_pins:
  - {name: registry.example.com, ip: 10.0.0.101}
k3s_storage_host_pins:
  - {name: nas.example.com, ip: 10.0.0.102}

# Passthrough disks, mounted by UUID (weisssrv.infra.zvol_mount). The same
# conventional name weisssrv.infra.proxmox_vm aliases to CREATE and attach them,
# so one host_vars block drives both. Set k3s_additional_disks to decouple.
vm_additional_disks:
  - name: postgres-data
    size: 10G
    zvol: ssd/appdata/authentik/postgres
    mount_point: /mnt/postgres-data
    fstype: ext4
```

Both join tokens are secrets: supply them from the site's secret store.
`k3s_agent_token` falls back to `k3s_token` when unset, which is convenient for
a test run and wrong for production — an agent token cannot promote a node.

## NVIDIA GPU agents (`k3s_gpu_node`)

Setting `k3s_gpu_node: true` on an agent includes `tasks/gpu.yml`, which enables
Debian `contrib`/`non-free`, adds NVIDIA's container-toolkit and CUDA apt repos
(the CUDA one via the SHA256-verified `cuda-keyring` deb), installs the
exact-pinned `nvidia-open` driver + `nvidia-container-toolkit`, and holds the
stack. k3s then auto-detects the `nvidia` container runtime — no containerd
template edit. A first install needs one VM reboot for the DKMS module.

The four artifact pins have **no default** and are asserted at the top of
`gpu.yml`; each aliases the conventional inventory-wide `nvidia_*` name, so one
site-wide version block feeds every GPU agent:

| Role variable | Aliases | Pins |
|---|---|---|
| `k3s_gpu_driver_version` | `nvidia_driver_version` | `nvidia-open=<version>` |
| `k3s_gpu_container_toolkit_version` | `nvidia_container_toolkit_version` | `nvidia-container-toolkit=<version>` |
| `k3s_gpu_cuda_keyring_version` | `nvidia_cuda_keyring_version` | the `cuda-keyring_<version>_all.deb` filename |
| `k3s_gpu_cuda_keyring_sha256` | `nvidia_cuda_keyring_sha256` | that deb's checksum, verified before dpkg runs it |

```yaml
# host_vars/<gpu-agent>.yml
k3s_gpu_node: true
# group_vars/all.yml — one block for the fleet
nvidia_driver_version: "610.43.02-1"
nvidia_container_toolkit_version: "1.19.1-1"
nvidia_cuda_keyring_version: "1.1-1"
nvidia_cuda_keyring_sha256: "d0d4ef98…"
```

`k3s_skip_gpu_install: true` renders the repo/component config but skips every
network/package task (and the assert above) — the escape hatch molecule and
check-mode runs use. `k3s_gpu_debian_sources_path` retargets the deb822 sources
file whose `Components:` line is normalized.

## metrics-server override (`k3s_metrics_server_override_enabled`)

k3s packages metrics-server as a single replica with no memory limit, and it is
the only metric source every HPA and the VPA recommender read: if that one pod
OOMs, HPAs freeze on their last replica count and look healthy. Enabling this
writes a `HelmChartConfig` into the first server's manifests dir raising the
replica count and bounding memory:

```yaml
k3s_metrics_server_override_enabled: true
k3s_metrics_server_replicas: 2                 # default
k3s_metrics_server_resources:                  # default
  requests: {cpu: 25m, memory: 128Mi}
  limits: {memory: 256Mi}
```

A `HelmChartConfig` only reaches the component where k3s packages it as a
HelmChart. Where k3s ships metrics-server as a static wrangler AddOn, the
override applies cleanly and changes nothing — so the role probes for
`HelmChart/metrics-server` in `kube-system` first and fails with that diagnosis
rather than leaving an inert file behind. On such a k3s the alternative is to
disable the packaged component (`k3s_disable`) and ship a full replacement
manifest. The same trap applies to CoreDNS, which is why a replica pin for it is
an in-cluster HPA rather than a `HelmChartConfig`.

## kube-apiserver audit logging (`k3s_audit_enabled`)

k3s ships apiserver audit logging **off**, so by default nothing in the cluster
records who read a Secret, who granted themselves a ClusterRole, or which
identity did it. Setting `k3s_audit_enabled: true` writes an audit policy to
each server and points the apiserver at it.

```yaml
k3s_audit_enabled: true
# Defaults, all overridable:
k3s_audit_log_path: /var/lib/rancher/k3s/server/logs/audit.log
k3s_audit_policy_path: /var/lib/rancher/k3s/server/audit-policy.yaml
k3s_audit_maxage: 30        # days
k3s_audit_maxbackup: 10     # rotated files kept
k3s_audit_maxsize: 100      # MB per file
k3s_audit_policy: {...}     # the full audit/v1 Policy — see defaults/main.yml
```

### What is logged

Rules are evaluated **in order, first match wins**, and the shipped policy has
**no catch-all** — an event matching no rule is not logged at all. That is what
keeps a security control from becoming a disk-space incident.

| Rule | Level | Why |
|---|---|---|
| `/healthz*`, `/livez*`, `/readyz*`, `/version`, `/metrics`, `/openapi/*` | `None` | probe traffic, several times a second, forever |
| `coordination.k8s.io/leases` in `kube-system` | `None` | leader-election renewals — a write every couple of seconds, no signal |
| `rbac.authorization.k8s.io` roles/clusterroles/(cluster)rolebindings, **write verbs** | `RequestResponse` | a privilege grant is the one case where the body *is* the evidence |
| the same RBAC resources, any other verb | `Metadata` | enumerating permissions is reconnaissance worth recording, but a `RequestResponse` on `list clusterroles` would dump the whole RBAC tree on every controller resync |
| core `secrets`, `configmaps`, `serviceaccounts` | `Metadata` | who touched which credential object, when, as whom — **never** the body, which would write Secret contents into a plaintext log |

The two `None` rules are noise suppression *and* a deny-first guard: they are
redundant while there is no catch-all, and load-bearing the moment anyone
appends one. Keep them first if you override `k3s_audit_policy`.

### Where it lands, and rotation

The log is written to `k3s_audit_log_path` on **that server's local disk** and
rotated by the apiserver itself (`maxsize` MB per file, `maxbackup` files kept,
anything older than `maxage` days discarded) — worst case
`maxsize x (maxbackup + 1)` ≈ 1.1 GB per server at the defaults. The role
creates the parent directory `0700` and writes the policy `0600`, both
root-owned: audit events name principals, namespaces and object names.

Nothing ships it off-node. `weisssrv.infra.alloy_host` reads **journald only**
(`loki.source.journal`), and the apiserver writes this file directly, so it is
not picked up by the existing log pipeline. Shipping it would need a
`loki.source.file` component that this role deliberately does not build — treat
the log as node-local forensic material, read with `jq` on the server.

### Restart implication

The policy file and the `kube-apiserver-arg` block in
`/etc/rancher/k3s/config.yaml` are both read **once at apiserver startup**, so
enabling — or editing the policy afterwards — notifies `Restart k3s` +
`Wait for k3s API healthy`. On a multi-server cluster that is a rolling
control-plane bounce with an API-VIP failover per node; run it in a deliberate
window with a healthy etcd quorum, and keep the servers play `serial: 1`.

The ordering matters and the role enforces it: `tasks/audit.yml` runs **before**
the server config template, because an apiserver whose `--audit-policy-file` is
missing or unparseable **exits at startup**. For the same reason the role
asserts `k3s_audit_policy` is an `audit.k8s.io/v1` `Policy` with at least one
rule before writing it — a malformed override should fail the play, not roll the
control plane into a crash loop.

Turning it back off removes the args from the config (another restart) and
leaves the now-unreferenced policy file behind, inert.

## Task Flow

```
1. Install prerequisites
2. Enable iscsid service
3. Create k3s config directory
3b. Raise kernel inotify ceilings (sysctl.d drop-in; live-applied on real nodes)
4. Mount additional persistent disks (if defined)
   ├─ Check if formatted
   ├─ Format if needed (ext4)
   ├─ Get filesystem UUID
   ├─ Create mount points
   └─ Add to /etc/fstab and mount
5. Include server or agent tasks based on k3s_role
6. Apply node labels
7. Apply node taints
```

## Files

- `tasks/main.yml` - Main orchestration (prerequisites, inotify sysctl, /etc/hosts pins, disks)
- `tasks/server.yml` - Server installation
- `tasks/agent.yml` - Agent installation (incl. agent-token migration)
- `tasks/install-script.yml` - Shared version detection + installer staging
- `tasks/gpu.yml` - NVIDIA driver/toolkit enablement (included when `k3s_gpu_node`)
- `tasks/audit.yml` - kube-apiserver audit policy (opt-in, servers only)
- `tasks/etcd-snapshot-offnode.yml` - Off-node etcd snapshot copy (opt-in)
- `tasks/etcd-snapshot-offnode-absent.yml` - Its de-provisioning path
- `tasks/metrics-server-override.yml` - metrics-server replicas/resources (opt-in)
- `templates/k3s-server-config.yaml.j2` - Server configuration
- `templates/k3s-agent-config.yaml.j2` - Agent configuration
- `templates/k3s-audit-policy.yaml.j2` - kube-apiserver audit policy
- `templates/kube-vip-manifest.yaml.j2` - Kube-vip DaemonSet
- `templates/metrics-server-helmchartconfig.yaml.j2` - metrics-server override
- `templates/k3s-etcd-snapshot-copy.{sh,service,timer}.j2` - Off-node snapshot copy
- `defaults/main.yml` - Default values
- `handlers/main.yml` - Service restart + Ready-gate handlers
- `molecule/default/` - Server scenario (bootstrap + join branches)
- `molecule/agent/` - Agent scenario (config, token migration)

## Companion roles

Applied by the caller's playbook, not as meta dependencies:
`weisssrv.infra.base`, `weisssrv.infra.postfix_null_client`,
`weisssrv.infra.alloy_host`, `weisssrv.infra.nfs_tls`,
`weisssrv.infra.proxmox_firewall`.

## Persistent storage

A passthrough block device (a ZFS zvol on the hypervisor, say) survives VM
recreation, and brings the host filesystem's snapshots and compression with it.
The role formats it, records its UUID in `/etc/fstab` and mounts it via
`weisssrv.infra.zvol_mount`.

## Operations

```bash
systemctl status k3s          # servers
systemctl status k3s-agent    # agents
journalctl -u k3s -f
kubectl get nodes
kubectl get pods -A --field-selector spec.nodeName=<node>
```

Scaling out is: add the node to the inventory, provision the VM, run the
playbook limited to it. Upgrading is: bump `k3s_version` and re-run, node by
node.
