# weisssrv.infra.base

Foundational system configuration applied to all managed hosts. Provides essential packages, Proxmox repository setup, SSH hardening, fail2ban intrusion prevention, user management, timezone configuration, and DNS settings.

## What this role manages

**Packages.** On a PVE host it disables the enterprise repos and enables
`pve-no-subscription` as a deb822 stanza pinned to the Proxmox archive keyring
through `Signed-By`. It installs `base_common_packages` everywhere and
`base_vm_packages` on KVM guests only.

**Admin user.** It creates the admin account with passwordless sudo, validated
with `visudo`. The SSH key list is additive by default: dropping a key does not
revoke it, and the run still reports converged. Set
`base_ssh_authorized_keys_exclusive: true` to make the list authoritative, which
also removes keys installed outside Ansible. The list is deployed in one call,
joined with real newlines; a literal backslash-n would collapse every key into
one malformed line. An empty list under `exclusive` is refused, because it
would strip every key from the host.

**SSH hardening.** A `00-hardening.conf` drop-in disables root login, password
and keyboard-interactive auth and X11 forwarding, and sets `MaxAuthTries 3` and
keepalives. First match wins, so it beats cloud-init drop-ins. The candidate is
merged with the host's real config in a temp dir and checked with `sshd -t`
before install. Afterwards `sshd -T` must show exactly one `Port`, so a stale
port line fails the run instead of leaving the old port open.
`ssh_permit_root_login` is normalized to sshd's spelling first, because an
unquoted YAML `no` arrives as a boolean.

**Fail2ban.** The sshd jail runs everywhere, the pveproxy jail where enabled,
and the recidive jail on physical hosts and VMs. Unprivileged LXC containers
lack `CAP_NET_ADMIN`, so they use `banaction = route` and skip recidive.
`base_fail2ban_ignoreip` networks are never banned.

**DNS.** The shared `resolv_conf` role writes an immutable `/etc/resolv.conf`.
A resolver host gets `127.0.0.1` once `dig @127.0.0.1` answers, and
`base_bootstrap_dns_servers` until then. Every other host gets `dns_servers`.
Unprivileged LXC containers cannot set the immutable flag, so the role warns
there.

**System.** It sets the timezone, using the symlink method in containers. It
masks `openipmi.service` on hosts without IPMI, whose init script otherwise
leaves systemd degraded. It turns unattended-upgrades off on every guest and
container, writing the `20auto-upgrades` knobs even when the package is absent.
NIC offloads belong to `weisssrv.infra.nic_tuning`.

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
| `base_skip_ssh_config` | Skip the sshd hardening drop-in | `false` |
| `base_skip_dns_config` | Skip `/etc/resolv.conf` management | `false` |
| `base_skip_timezone_config` | Skip the timezone | `false` |
| `base_skip_sudoers_validation` | Skip `visudo -cf` on the admin sudoers file | `false` |

## Dependencies

None — this is the foundational role. It includes
`weisssrv.infra.resolv_conf` for /etc/resolv.conf.
