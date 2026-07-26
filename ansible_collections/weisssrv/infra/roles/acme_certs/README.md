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
| `acme_certs_ssh_key_dir` / `_ssh_key_path` | Where the key lives on this host | no (derived) |
| `acme_certs_local_cert_dir` / `_local_cert_group` | Local install path + reader group | no (`/etc/ssl/private`, `root`) |
| `acme_certs_local_reload_command` | Reload for a local consumer of the cert; empty omits the block | no (`""`) |
| `acme_certs_key_from` | `from="..."` source pin on the distributed key | no (`""`) |
| `acme_certs_distribute_pubkeys` | Seed the targets; `false` renders locally only | no (`true`) |
| `acme_certs_skip_distribution` | Skip the proactive push at the end | no (`false`) |
| `acme_certs_receiver_path` | Receiver path on each sudo target | no (`/usr/local/sbin/cert-receive`) |
| `acme_certs_sh_version` / `_sh_tarball_sha256` | Pinned acme.sh release | no |
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
    restart_service: AdGuardHome  # or restart_command: <verbatim command>
    ssh_user: <user>              # defaults to acme_certs_ssh_user
    ssh_port: 22
    ssh_no_sudo: false            # true = appliance, legacy scp push
```

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
hard 64 KiB cap, splits at the boundary (it assigns the output filenames —
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

`--server letsencrypt` is passed explicitly so the command also works against a
pre-existing acme.sh install that this role did not pin (acme.sh 3.x otherwise
defaults to ZeroSSL, which a Let's-Encrypt-only CAA record would refuse).

## Distribution on every run

Every run invokes the distribution script when the local cert exists, targets
are defined and the run is not in check mode. A target is skipped only when its
post-reload `.applied-fullchain.sha256` marker matches the cert being pushed
**and** the remote `fullchain.pem` and `privkey.pem` still hash-match the
source. So an unchanged, successfully-applied cert restarts nothing, while a
target that is missing the cert, holds an older one, was rebuilt, or whose
previous reload failed gets the full push.

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

- private key `0600`; distributed key `0640`, cert `0644`
- the distribution key is locked to the forced-command receiver on sudo targets
  — no shell, no arbitrary sudo
- the receiver validates every bundle (parse, expiry, key-matches-cert, SAN,
  chain-to-truststore) before installing anything
- all secret-handling tasks use `no_log`
