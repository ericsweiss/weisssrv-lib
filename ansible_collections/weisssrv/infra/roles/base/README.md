# weisssrv.infra.base

Foundational system configuration applied to all managed hosts. Provides essential packages, Proxmox repository setup, SSH hardening, fail2ban intrusion prevention, user management, timezone configuration, and DNS settings.

## What This Role Manages

### Package Management
- Proxmox repositories on PVE hosts (enterprise repos disabled, community
  `pve-no-subscription` enabled as a deb822 `.sources` stanza pinned to the
  Proxmox archive keyring via `Signed-By`)
- Core system packages (curl, wget, neovim, htop, tmux, git, jq, unzip, rsync, net-tools, dnsutils, ca-certificates, gnupg, lsb-release, sudo)
- VM-specific packages (qemu-guest-agent) -- automatically detected and installed only on KVM guests
- Apt cache updates with 1-hour validity window
- unattended-upgrades disabled on VMs and containers (updates are managed via
  controlled Task/Ansible workflows instead)

### User Management
- Admin user creation and configuration
- Sudo group membership
- Passwordless sudo via `/etc/sudoers.d/` (validated with visudo)
- SSH authorized keys, optionally with `from=` network restrictions
- Home directory and `.ssh` directory creation with correct permissions

### SSH Hardening
- Disable root login
- Disable password authentication (key-based only)
- Enable pubkey authentication
- Disable challenge-response authentication
- Disable X11 forwarding
- MaxAuthTries set to 3
- ClientAlive keepalive (300s interval, 2 max)
- Written as a `00-hardening.conf` drop-in under `/etc/ssh/sshd_config.d/`
  (first-match-wins, so it beats cloud-init drop-ins); the merged config is
  validated (`sshd -t`) before install and asserted effective via `sshd -T`

### Fail2ban
- sshd jail enabled on all hosts (aggressive mode, systemd backend)
- pveproxy jail on Proxmox hosts (`base_fail2ban_pveproxy_enabled`)
- Recidive jail for repeat offenders on physical/VM hosts
- LXC containers use `banaction = route` (blackhole routes) because
  unprivileged containers lack CAP_NET_ADMIN for iptables/nftables; the
  recidive jail is disabled there — trade-offs documented in
  `tasks/fail2ban.yml`
- Networks listed in `base_fail2ban_ignoreip` are never banned

### DNS Configuration
- DNS servers configured via `/etc/resolv.conf` (rendered by the shared
  `resolv_conf` role)
- Smart DNS selection:
  - a **resolver host** (`base_is_resolver_host: true`) gets `127.0.0.1` once
    its own resolver answers (probed with `dig @127.0.0.1`), and
    `base_bootstrap_dns_servers` only while it does not — the first-deploy
    chicken-and-egg, since the resolver roles run later in the play
  - every other host gets `dns_servers`
- Immutable resolv.conf (`chattr +i`, managed inside the `resolv_conf` role
  via `resolv_conf_immutable: true`) to prevent overwrites by DHCP/systemd.
  On unprivileged LXC containers the immutable flag cannot be set (no
  CAP_LINUX_IMMUTABLE); the role warns and relies on the file being
  Ansible-managed there.

### System Configuration
- Timezone (`Etc/UTC` by default; symlink-only method in containers)
- VM guest agent enablement (qemu-guest-agent service)
- openipmi.service masked on hosts without IPMI hardware (its LSB init script
  otherwise fails at boot and leaves systemd degraded)
- unattended-upgrades turned off on VMs and containers — the
  `/etc/apt/apt.conf.d/20auto-upgrades` knobs are written whether or not the
  package is installed, so a later `apt install unattended-upgrades` cannot
  come up enabled

> **NIC offloads are not this role's business.** They are owned by
> **`weisssrv.infra.nic_tuning`** (declarative `nic_tuning_overrides`). This
> role only removes the `atlantic-gro-fix` and `e1000e-tso-fix` oneshot units it
> used to install, so a host cannot end up with two owners of the same offload
> settings. A host that needs the e1000e TSO/GSO/GRO workaround must declare it
> in `nic_tuning_overrides`.

## Configuration

### Required Variables

These are **collection-wide site inputs** — several roles read them, so they
are deliberately not role-prefixed:

```yaml
admin_user: ops                  # `root` (the default) manages no admin user
admin_email: ops@example.com     # used by fail2ban notifications
ssh_port: 22
ssh_permit_root_login: "no"      # "prohibit-password" where migration needs it
ssh_password_authentication: false
ssh_pubkey_authentication: true

# `from=` restrictions are strongly recommended
ssh_authorized_keys:
  - 'from="10.0.0.0/24,100.64.0.0/10" ssh-ed25519 AAAA... admin'

dns_servers: [10.0.0.150, 10.0.0.160]
timezone: Etc/UTC
```

`tasks/ssh.yml` refuses to write the hardening drop-in when the combination
would lock SSH out: `ssh_permit_root_login: "no"` **and**
`ssh_password_authentication: false` **and** no managed admin user with a key
(`admin_user` left at `root`, or `ssh_authorized_keys` empty). Any one of those
three is a surviving login path — password authentication counts because it
keeps every pre-existing account reachable, which is how a host whose keys come
from cloud-init or image baking stays reachable. Either provision the account
and key, relax `ssh_permit_root_login`, or leave password auth on.

The condition is `base_ssh_login_path_survives` in `defaults/main.yml` — one
expression, asserted by `tasks/ssh.yml` and driven case-by-case by the
accept/reject matrix in `molecule/default/verify.yml` (which loads that same
defaults file), so the guard cannot drift from its tests.

Role-prefixed gates and the DNS bootstrap knobs:

```yaml
base_is_resolver_host: false         # true where the resolver itself runs
base_bootstrap_dns_servers: [1.1.1.1, 8.8.8.8]
base_resolver_probe_name: example.com
base_skip_ssh_config: false
base_skip_dns_config: false
base_skip_timezone_config: false
base_skip_sudoers_validation: false  # skips `visudo -cf`
```

Package lists (`base_common_packages`, `base_vm_packages`) and the full fail2ban
knob set (`base_fail2ban_*`: jail toggles, ban/find times, retry counts,
ignoreip, optional email notifications) live in `defaults/main.yml`.

## Scope

Apply it to every managed host. It gives them:
1. Consistent package sets
2. Hardened SSH configuration
3. Fail2ban intrusion prevention
4. Correct admin user setup
5. Proper DNS resolution
6. Correct timezone

## Task Flow

```
1. Configure Proxmox repositories (PVE hosts only)
2. Update apt cache (1-hour validity)
3. Install common packages
4. Detect virtualization (KVM guest / container facts)
   └─ KVM: install qemu-guest-agent, enable service
5. Disable unattended-upgrades (VMs and containers)
6. Mask openipmi.service (no-IPMI hosts)
7. Create admin user, .ssh directory, authorized_keys, passwordless sudo
8. Include SSH hardening tasks
   ├─ Render drop-in candidate + validate merged config (sshd -t)
   ├─ Install /etc/ssh/sshd_config.d/00-hardening.conf (restart sshd)
   └─ Assert effective values via sshd -T
9. Set timezone (hwclock method, or symlink in containers)
10. Include DNS configuration tasks
    ├─ Probe the local resolver on a resolver host (keep 127.0.0.1 when healthy)
    ├─ Determine DNS servers
    └─ Include resolv_conf role (writes file, manages immutable flag)
11. Remove orphaned NIC offload fix units (atlantic-gro-fix, e1000e-tso-fix)
12. Include fail2ban tasks (install, jail.local, filters, service)
```

## Files

- `tasks/main.yml` - Main task orchestration
- `tasks/proxmox-repos.yml` - Proxmox repository configuration
- `tasks/ssh.yml` - SSH hardening configuration
- `tasks/dns.yml` - DNS server selection (delegates to the `resolv_conf` role)
- `tasks/fail2ban.yml` - Fail2ban installation and configuration
- `templates/sshd-hardening.conf.j2` - SSH hardening drop-in
- `templates/jail.local.j2` / `templates/proxmox.conf.j2` - Fail2ban config
- `weisssrv.infra.resolv_conf` - renders /etc/resolv.conf (shared role)
- `defaults/main.yml` - Default variable values
- `handlers/main.yml` - Service restart handlers

## Dependencies

None — this is the foundational role. It includes
`weisssrv.infra.resolv_conf` for /etc/resolv.conf.

## Security

- SSH password authentication disabled by default
- Root login disabled (key-only on Proxmox for migration/replication)
- SSH keys carry whatever `from=` restriction the site puts in `ssh_authorized_keys`
- Fail2ban bans brute-force sources on SSH (and pveproxy on Proxmox hosts)
- Sudoers configuration validated before applying
- SSH configuration validated before install and asserted effective after
- Proxmox community repo pinned to the archive keyring (`Signed-By`)
- resolv.conf made immutable to prevent tampering where the platform allows it
  (not enforceable in unprivileged LXC containers — the resolv_conf role warns
  there instead)

## Idempotency

- Package installation is idempotent
- User creation checks for existence first
- SSH configuration changes only trigger restart if modified
- DNS immutability only reports changed on a real absent-to-present transition
- A healthy resolver host keeps its localhost resolver on re-runs
- Apt cache update uses `cache_valid_time` to avoid unnecessary refreshes
