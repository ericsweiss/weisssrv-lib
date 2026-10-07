# weisssrv.infra.acme_certs

Issues a Let's Encrypt **wildcard** certificate with acme.sh over DNS-01 and
distributes it to a list of target hosts over a locked-down SSH channel.

Run it on the cert authority host (`acme_certs_enabled: true`) — everywhere else
the role is a no-op.

## What it manages

- acme.sh, installed from a **pinned release tarball** (not the single-file
  installer: only the tarball ships `dnsapi/`, and a hook missing at install
  time is never fetched later — a single-file install can neither issue nor
  renew over DNS-01) with Let's Encrypt pinned as the default CA
- the renewal cronjob, and `homelab-cert-reload.sh` as acme.sh's `--reloadcmd`
  — re-asserted on every run, so a cert that arrived by another route (manual
  `--install-cert`, a restore, a pre-role host) does not renew without
  distributing
- the distribution key pair, plus each target's forced-command receiver,
  sudoers drop-in, pinned `authorized_keys` entry and pinned host key
- installation of the issued cert into `acme_certs_local_cert_dir`

## Variables

| Variable | Meaning | Required |
|---|---|---|
| `acme_certs_enabled` | Run here (the authority host) | no (`false`) |
| `acme_certs_domain` | Base domain; the cert covers it and `*.<domain>` | yes |
| `acme_certs_email` | ACME account email | yes |
| `acme_certs_ssh_private_key` / `_ssh_public_key` | Distribution key pair | yes |
| `acme_certs_ssh_user` | Login user on the targets, and owner of the key here | no (`root`) |
| `acme_certs_ssh_key_dir`, `acme_certs_ssh_key_path` | Where the key lives on this host | no (derived) |
| `acme_certs_local_cert_dir`, `acme_certs_local_cert_group` | Local install path and reader group | no (`/etc/ssl/private`, `root`) |
| `acme_certs_textfile_dir` | Where the renewal/distribution metrics land; aliases `node_exporter_host_textfile_dir` | no (`/var/lib/node_exporter`) |
| `acme_certs_local_reload_command` | Reload for a local consumer of the cert; empty omits the block | no (`""`) |
| `acme_certs_key_from` | `from="..."` source pin on the distributed key | no (`""`) |
| `acme_certs_distribute_pubkeys` | Seed the targets; `false` renders locally only | no (`true`) |
| `acme_certs_skip_distribution` | Skip the proactive push at the end | no (`false`) |
| `acme_certs_distribution_check_enabled` | Install the daily distribution check timer | no (`true`) |
| `acme_certs_distribution_check_schedule` | `OnCalendar` for the check timer | no (`*-*-* 05:40:00`) |
| `acme_certs_distribution_check_random_delay` | `RandomizedDelaySec` for the check timer | no (`30m`) |
| `acme_certs_distribution_check_nice` | `Nice` for the check service | no (`10`) |
| `acme_certs_distribution_check_timeout` | `TimeoutStartSec` for the check service | no (`10m`) |
| `acme_certs_distribution_check_retries` | Retries for a check-mode probe that fails, before the target's gauge is written 0 | no (`2`) |
| `acme_certs_distribution_check_retry_delay` | Seconds between those retries | no (`10`) |
| `acme_certs_receiver_path` | Receiver path on each sudo target | no (`/usr/local/sbin/cert-receive`) |
| `acme_certs_sh_version`, `acme_certs_sh_tarball_sha256` | Pinned acme.sh release and its checksum | no |
| `acme_certs_stage_dir` | Root-owned 0700 dir the release tarball is staged in, removed after the install | no (`/run/acme-certs-install`) |
| `acme_certs_dns_hook` | acme.sh dnsapi hook used for DNS-01 (any hook the pinned tarball ships) | no (`dns_cf`) |
| `acme_certs_ca_server` | ACME CA acme.sh is pinned to (any name acme.sh accepts, or a CA directory URL) | no (`letsencrypt`) |
| `acme_certs_distribution_targets` | Target list (schema below) | no (`[]`) |

### Target schema

```yaml
acme_certs_distribution_targets:
  - host: dns-02                  # inventory hostname (also the delegate)
    ip: 10.0.0.160                # address the push connects to
    host_key: "ssh-ed25519 AAAA..."   # REQUIRED — see host-key pinning
    cert_dir: /opt/AdGuardHome/certs
    owner: root
    group: adguard
    cert_mode: "0644"
    key_mode: "0640"
    restart_service: AdGuardHome  # REQUIRED — or restart_command: <verbatim command>
    ssh_user: <user>              # defaults to acme_certs_ssh_user
    ssh_port: 22
    ssh_no_sudo: false            # true = appliance, legacy scp push
```

`restart_command` runs verbatim on the target and must invoke `sudo` itself
where it needs root: the push cannot prepend `sudo`, because a compound shell
form such as `if … fi` is not something sudo can execute. An empty or
whitespace-only `restart_command` counts as unset and falls back to
`restart_service`.

`key_mode` is group-readable above only because the consuming service runs as a
non-root user (`group: adguard`). Use `0600` wherever the key can stay
root-only — in particular on an NFS-over-TLS host, where
`weisssrv.infra.nfs_tls` asserts the key is `0600`/`0400` and fails the deploy
on anything looser.

## Host-key pinning (required)

Distribution pushes wildcard **private-key material**, so the channel runs with
`StrictHostKeyChecking=yes` and never `accept-new`. Every target therefore needs
a `host_key`; the role asserts it and fails loudly when one is missing, then
pre-loads all of them into `/root/.ssh/known_hosts`.

```bash
ssh-keyscan -t ed25519 -p <port> <ip>
```

When a target is rebuilt, the next push fails with `REMOTE HOST IDENTIFICATION
HAS CHANGED`. Capture the new key, replace the stale value, re-run.

## Forced-command receiver (sudo targets)

A sudo target never grants the distribution key a shell. Each gets:

- **`cert-receive`** (`acme_certs_receiver_path`, mode `0500`) — every
  operational parameter (cert dir, owner/group/modes, reload command, expected
  domain) is baked in at deploy time from that target's entry, so the client
  controls only the cert bytes on stdin.
- **`/etc/sudoers.d/cert-receive`** — `<ssh_user> ALL=(root) NOPASSWD:
  <receiver>` (+ `!requiretty`), validated with `visudo -cf`. The rule permits
  exactly the receiver, nothing else.
- **authorized_keys pinning** — the pubkey is installed with
  `command="sudo <receiver>",restrict` (plus `from=` when `acme_certs_key_from`
  is set). A leaked key can only install a validated cert and run that one
  baked-in reload.

**Pipe protocol.** `homelab-cert-reload.sh` pushes each sudo target in a single
SSH round-trip: `fullchain.pem`, a fixed non-PEM boundary line, then
`privkey.pem` on the forced command's stdin. The receiver reads stdin with a
hard 64 KiB cap and **rejects** anything larger (it reads one byte past the cap,
so an oversized bundle fails as oversized rather than as a truncated PEM), splits
at the boundary (it assigns the output filenames —
nothing from stdin becomes a path), then validates before trusting: both parse,
the cert is unexpired, the key matches the leaf, the SAN covers
`*.<acme_certs_domain>`, and the leaf chains to a CA already in the host
truststore (so a well-formed but untrusted wildcard is rejected). Only then does
it install each file via same-directory temp + rename (no reader sees a torn
file) with the baked-in ownership/modes, run the reload, and answer
`OK` / `unchanged` / `FAIL`. The applied marker is written only after a clean
reload, so a failed reload self-heals on the next push. No scp, no remote
mktemp/chown, no pre-check probes.

**Appliance exception.** A target with `ssh_no_sudo: true` keeps the legacy
scp+ssh push: its `authorized_keys` is operator-managed, so the role can deploy
neither the receiver nor the sudoers rule there.

## Unreachable targets do not stop the run

Target seeding is a looped **include** around single delegated tasks, never a
looped `delegate_to`: with a looped delegate, one unreachable target marks the
loop's aggregate result unreachable for the executing host and the strategy
drops that host from the play — on an authority host running in a batch of one,
that silently skips distribution to every *reachable* target, and everything
after it. Each target is probed first; unreachable ones are listed at the end
and left unseeded. A genuinely-down target still turns the run red later: the
distribution script exits non-zero when it cannot reach one.

## Manual issuance

The role prints instructions when acme.sh is installed but no cert exists yet:

```bash
export CF_Token=...            # credentials the dns_cf hook expects
export CF_Account_ID=...

/root/.acme.sh/acme.sh --issue --dns dns_cf --server letsencrypt \
  --keylength ec-256 \
  -d "<domain>" -d "*.<domain>"
```

Another DNS provider is `acme_certs_dns_hook` plus that hook's own credential
environment: the role checks for the hook, names it in these instructions, and
does not otherwise care which provider signs the challenge.

`--server` is passed explicitly so the command also works against a pre-existing
acme.sh install that this role did not pin (acme.sh 3.x otherwise defaults to
ZeroSSL, which a Let's-Encrypt-only CAA record would refuse). Another CA is
`acme_certs_ca_server`; the role pins it at install time and re-asserts it on
every run.

## Distribution on every run

Every run invokes the distribution script when the local cert exists, targets
are defined and the run is not in check mode. A target is skipped only when its
post-reload `.applied-fullchain.sha256` marker matches the cert being pushed
**and** the remote `fullchain.pem` and `privkey.pem` still hash-match the
source. So an unchanged, successfully-applied cert restarts nothing, while a
target that is missing the cert, holds an older one, was rebuilt, or whose
previous reload failed gets the full push.

## Appliance push (ssh_no_sudo)

The legacy scp push stages `fullchain.pem` and `privkey.pem` in a per-run 0700
directory on the target rather than in world-readable `/tmp`, so the unencrypted
wildcard key is never exposed to other local users. The install steps are
chained with `&&`, so a partial move never reaches the reload.

## Daily distribution check

`homelab-cert-check.timer` runs `homelab-cert-reload.sh --check` once a day, so
the per-target gauges move between renewals instead of ageing from the last one.
The check pushes nothing, runs no reload command and leaves `cert_renewal.prom`
alone. What it proves per target:

- Sudo targets: an empty bundle reaches the forced-command receiver, which
  refuses it. That answer proves the transport, the pinned key, the forced
  command and sudo.
- `ssh_no_sudo` targets: the hash comparison the real push starts with. A target
  that is unreachable, or that is not carrying the applied certificate, reports
  0.

A run with any failed target exits 2, so the unit is marked failed as well.
`acme_certs_distribution_check_enabled: false` removes both units.

## Metrics

`homelab-cert-reload.sh` writes two node_exporter textfiles under
`acme_certs_textfile_dir`, which aliases `node_exporter_host_textfile_dir`
(default `/var/lib/node_exporter`):

- `cert_renewal.prom` — `cert_renewal_last_run_success`,
  `cert_renewal_last_run_duration_seconds`,
  `cert_renewal_last_success_timestamp_seconds`, and
  `cert_local_expiry_timestamp_seconds` (the on-disk cert's `notAfter`, emitted
  on failed runs too so an expiry alert's `absent()` clause does not false-fire).
  A failed run re-emits the previous success timestamp, so staleness measures
  time since the last success rather than disappearing.
  `cert_renewal_last_run_success` covers the local certificate only: the renewal
  and the local reload. A dead distribution target leaves it at 1 and is
  reported by the distribution gauges below.
- `cert_distribution_targets.prom` —
  `cert_distribution_target_last_run_success{host="…"}`, so one dead target is
  visible instead of being collapsed into the run-level bit, plus
  `cert_distribution_last_run_failed_targets`, the number of targets that failed
  on the last run, so an alert needs no regex over host labels. Written to a
  temp file and renamed, and a failed write logs `daemon.err` under the tag
  `homelab-cert-reload-metrics`. The daily check rewrites this file, so its
  gauges age by a day at most while the timer is enabled.

Alert on both: `cert_renewal_last_run_success == 0` is a local failure, and
`cert_distribution_last_run_failed_targets > 0` is a target that did not take
the new cert.

## Operations

```bash
/root/.acme.sh/acme.sh --list
openssl x509 -in <local_cert_dir>/fullchain.pem -noout -dates
sudo /usr/local/sbin/homelab-cert-reload.sh                    # push now
/root/.acme.sh/acme.sh --renew -d <domain> -d '*.<domain>' --force

# End-to-end channel test. The key is forced to the receiver, so this runs
# cert-receive and expects "FAIL: empty bundle" — proving SSH + forced command
# + sudo all work.
ssh -i <ssh_key_path> <ssh_user>@<target> </dev/null
```

## Security

- local private key `0600`; the distributed key's mode is each target's
  `key_mode` (`0600` unless a non-root service must read it), cert `0644`
- the distribution key is locked to the forced-command receiver on sudo targets
  — no shell, no arbitrary sudo
- the receiver validates every bundle (parse, expiry, key-matches-cert, SAN,
  chain-to-truststore) before installing anything
- all secret-handling tasks use `no_log`
