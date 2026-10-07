# weisssrv.infra.base

Foundational system configuration applied to all managed hosts. Provides essential packages, Proxmox repository setup, SSH hardening, fail2ban intrusion prevention, user management, timezone configuration, and DNS settings.

## What this role manages

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
- SSH authorized keys, optionally with `from=` network restrictions. The whole
  list is deployed in ONE `authorized_key` call (newline-joined) — a looped
  `exclusive: true` would leave only the last key on the host
- Home directory and `.ssh` directory creation with correct permissions

### SSH Hardening
- Disable root login
- Disable password authentication (key-based only)
- Enable pubkey authentication
- Disable keyboard-interactive authentication (`KbdInteractiveAuthentication no`)
- Disable X11 forwarding
- MaxAuthTries set to 3
- ClientAlive keepalive (300s interval, 2 max)
- Written as a `00-hardening.conf` drop-in under `/etc/ssh/sshd_config.d/`
  (first-match-wins, so it beats cloud-init drop-ins)
- Validated before install: the candidate is rendered into a temp dir, merged
  with the host's real config there and checked with `sshd -t`, so a bad value
  never lands on disk. Validating after install would leave the broken file
  behind for the next sshd restart
- Asserted effective afterwards with `sshd -T`. `Port` is additive in
  sshd_config, so the assert requires exactly one distinct port line: a stale
  `Port` from the monolithic config or another drop-in would otherwise keep the
  old SSH port listening, and the role fails closed instead
- `ssh_permit_root_login` is normalized to sshd's own spelling before it is
  rendered or compared. An unquoted `no` in YAML arrives as a boolean, which
  would write `PermitRootLogin False` and read as a surviving login path in the
  lockout guard

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
- unattended-upgrades turned off on any hypervisor's guest and on containers
  (`base_is_virtual_machine` is `virtualization_role == 'guest'`; the guest
  agent is gated on the narrower KVM-only `base_is_kvm_guest`) — the
  `/etc/apt/apt.conf.d/20auto-upgrades` knobs are written whether or not the
  package is installed, so a later `apt install unattended-upgrades` cannot
  come up enabled

### Kernel command line

`base_kernel_cmdline_args` appends extra kernel boot parameters through
`/etc/default/grub.d/99-base-kernel-cmdline.cfg`, which `update-grub` folds
into the boot entries. The default is empty, and emptying the list again
removes the drop-in. One bare token per entry: entries are joined with spaces,
so an entry carrying whitespace is rejected rather than silently split into a
different set of parameters.

Use it for any parameter something else depends on, such as `slub_nomerge` when
per-cache slab metrics have to be attributable. Applied by hand, that parameter
survives only until the next reinstall, and whatever reads those metrics then
goes quietly wrong.

The drop-in is read only by `update-grub`. A host that takes its command line
from `/etc/kernel/cmdline` (systemd-boot, or `proxmox-boot-tool` in UEFI mode)
fails the play instead of staging a file that no boot loader reads. The
parameters apply at the next reboot; the role prints a reboot warning and never
reboots.

> **NIC offloads are not this role's business.** They are owned by
> **`weisssrv.infra.nic_tuning`** (declarative `nic_tuning_overrides`), and a
> host needing the e1000e TSO/GSO/GRO workaround declares it there.

## Configuration

### Required Variables

These are **collection-wide site inputs** — several roles read them, so they
are deliberately not role-prefixed:

```yaml
admin_user: ops                  # `root` (the default) manages no admin user
admin_email: ops@example.com     # used by fail2ban notifications
ssh_port: 22
ssh_permit_root_login: "no"      # "prohibit-password" where migration needs it
                                 # unquoted no/yes (YAML booleans) are accepted too
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

### Variables

Every key in `defaults/main.yml`. The first group aliases a collection-wide site
input, so a site can set either name.

| Variable | Purpose | Default |
|---|---|---|
| `base_admin_user` | Admin account the role creates, sudo-enables and keys; `root` manages no admin user | `admin_user`, else `root` |
| `base_admin_email` | Address fail2ban notifications go to | `admin_email`, else `root@localhost` |
| `base_ssh_port` | Port the hardening drop-in sets, and the port the sshd jail watches | `ssh_port`, else `22` |
| `base_ssh_permit_root_login` | `PermitRootLogin` value as the site spells it | `ssh_permit_root_login`, else `no` |
| `base_ssh_permit_root_login_effective` | The same value normalized to sshd's spelling; used for rendering and comparison | derived |
| `base_ssh_password_authentication` | `PasswordAuthentication` | `ssh_password_authentication`, else `false` |
| `base_ssh_pubkey_authentication` | `PubkeyAuthentication` | `ssh_pubkey_authentication`, else `true` |
| `base_ssh_service_name` | sshd unit name used by the restart handler | `ssh` |
| `base_ssh_authorized_keys` | Keys installed for the admin user | `ssh_authorized_keys`, else `[]` |
| `base_ssh_authorized_keys_exclusive` | Make the key list authoritative | `false` |
| `base_ssh_login_path_survives` | Lockout guard expression asserted by `tasks/ssh.yml` | derived |
| `base_timezone` | System timezone | `timezone`, else `Etc/UTC` |
| `base_common_packages` | Packages installed on every host | curl, wget, neovim, htop, tmux, git, jq, unzip, rsync, net-tools, dnsutils, ca-certificates, gnupg, lsb-release, sudo |
| `base_vm_packages` | Packages installed on KVM guests only | `[qemu-guest-agent]` |
| `base_dns_servers` | Resolvers written to `/etc/resolv.conf` on a non-resolver host | `dns_servers`, else `base_bootstrap_dns_servers` |
| `base_is_resolver_host` | This host runs the site resolver | `false` |
| `base_bootstrap_dns_servers` | Resolvers a resolver host uses until its own answers | `[1.1.1.1, 8.8.8.8]` |
| `base_resolver_probe_name` | Name the resolver probe queries | `example.com` |
| `base_fail2ban_enabled` | Install and configure fail2ban | `true` |
| `base_fail2ban_ignoreip` | Never-banned networks; add the site LAN and any VPN range | `[127.0.0.1/8, ::1]` |
| `base_fail2ban_default_bantime`, `base_fail2ban_default_findtime`, `base_fail2ban_default_maxretry` | Jail defaults in `jail.local` | `1h`, `10m`, `5` |
| `base_fail2ban_sshd_enabled` | sshd jail | `true` |
| `base_fail2ban_sshd_port` | Port the sshd jail watches | `base_ssh_port` |
| `base_fail2ban_sshd_maxretry`, `base_fail2ban_sshd_bantime`, `base_fail2ban_sshd_findtime` | sshd jail tuning | `5`, `1h`, `10m` |
| `base_fail2ban_recidive_enabled` | Repeat-offender jail; off on containers | `true` |
| `base_fail2ban_recidive_bantime`, `base_fail2ban_recidive_findtime`, `base_fail2ban_recidive_maxretry` | Recidive jail tuning | `1w`, `1d`, `3` |
| `base_fail2ban_pveproxy_enabled` | pveproxy jail; enable on Proxmox hosts | `false` |
| `base_fail2ban_pveproxy_port` | Port the pveproxy jail watches | `8006` |
| `base_fail2ban_pveproxy_maxretry`, `base_fail2ban_pveproxy_bantime`, `base_fail2ban_pveproxy_findtime` | pveproxy jail tuning | `5`, `1h`, `10m` |
| `base_fail2ban_email_enabled` | Send ban notifications through the local relay | `false` |
| `base_fail2ban_email_dest`, `base_fail2ban_email_sender` | Notification addresses | `base_admin_email`, `fail2ban@<host>` |
| `base_fail2ban_email_action` | fail2ban action used for notifications | `%(action_mwl)s` |
| `base_kernel_cmdline_args` | Extra kernel boot parameters appended to `GRUB_CMDLINE_LINUX_DEFAULT`; one bare token per entry | `[]` |
| `base_skip_boot_update` | Skip `update-grub` and the reboot warning | `false` |
| `base_skip_ssh_config` | Skip the sshd hardening drop-in | `false` |
| `base_skip_dns_config` | Skip `/etc/resolv.conf` management | `false` |
| `base_skip_timezone_config` | Skip the timezone | `false` |
| `base_skip_sudoers_validation` | Skip `visudo -cf` on the admin sudoers file | `false` |

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
11. Include fail2ban tasks (install, jail.local, filters, service)
```

## Files

- `tasks/main.yml` - Main task orchestration
- `tasks/proxmox-repos.yml` - Proxmox repository configuration
- `tasks/ssh.yml` - SSH hardening configuration
- `tasks/dns.yml` - DNS server selection (delegates to the `resolv_conf` role)
- `tasks/fail2ban.yml` - Fail2ban installation and configuration
- `tasks/kernel-cmdline.yml` - Extra kernel boot parameters (GRUB drop-in)
- `templates/sshd-hardening.conf.j2` - SSH hardening drop-in
- `templates/jail.local.j2` / `templates/proxmox.conf.j2` - Fail2ban config
- `templates/grub-kernel-cmdline.cfg.j2` - GRUB drop-in for the extra parameters
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
- `ssh_authorized_keys` is **additive** by default: dropping a key from the list
  does not revoke it on hosts the role already touched, and the run still
  reports converged. Set `base_ssh_authorized_keys_exclusive: true` to make the
  list authoritative (it then also removes keys installed outside Ansible), or
  remove the key by hand
- The key list is deployed in one call, newline-joined, and the separator must
  be a real newline. `authorized_key` splits a multi-key value on newlines, so a
  literal backslash-n collapses every key into one malformed line — under
  `exclusive` that would be the only line left
- An empty list combined with `base_ssh_authorized_keys_exclusive: true` is
  refused rather than applied: it would strip every key from an SSH-managed
  host. A deliberate full revocation belongs to console access
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
