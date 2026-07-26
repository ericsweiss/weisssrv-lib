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
  Toggle with `k3s_inotify_tuning`; see the note below and the role README

### Persistent Storage
- Additional disk formatting (passthrough block devices, e.g. ZFS zvols)
- Filesystem creation (ext4)
- UUID-based mounting in /etc/fstab
- Mount point creation and management

### K3s Installation
- Version checking and upgrading (pinned installer script, optional sha256 pin
  via `k3s_install_script_checksum`)
- Server installation (with embedded etcd, `secrets-encryption: true`,
  WireGuard flannel backend — see the role README)
- Agent installation (connects to API VIP with the lower-privilege agent
  token; existing agents are migrated off the server token — see the role README)
- Kube-vip manifest deployment (first server only)
- /etc/hosts pins: container-registry hostname → internal Traefik VIP
  (`k3s_registry_host_pins`) and NAS storage hostname for NFS-over-TLS PVs
  (`k3s_storage_host_pins`)
- Node label application
- Node taint application
- Off-node etcd snapshot copy (opt-in, servers only): a systemd timer that
  copies the newest local etcd snapshot to an NFS export (by hostname, over
  TLS) and emits an `etcd_snapshot_last_copy_timestamp_seconds`
  textfile metric for the `EtcdSnapshotStale` alert — off by default via
  `k3s_etcd_snapshot_offnode_enabled` (see the role README and defaults for the
  companion NFS export + `node_exporter_host` + `nfs_tls`/tlshd on the servers
  it needs — the `xprtsec=tls` mount hangs without the TLS handshake daemon)

## Configuration

```yaml
# Cluster
k3s_api_vip: "10.0.0.161"          # required — the kube-vip API address
k3s_token: "<server/cluster join token>"
k3s_agent_token: "<lower-privilege agent join token>"
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
- `tasks/etcd-snapshot-offnode.yml` - Off-node etcd snapshot copy (opt-in)
- `templates/k3s-server-config.yaml.j2` - Server configuration
- `templates/k3s-agent-config.yaml.j2` - Agent configuration
- `templates/kube-vip-manifest.yaml.j2` - Kube-vip DaemonSet
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

```bash
kubectl get pods -A --field-selector spec.nodeName=<node>
```

Scaling out is: add the node to the inventory, provision the VM, run the
playbook limited to it. Upgrading is: bump `k3s_version` and re-run, node by
node.
