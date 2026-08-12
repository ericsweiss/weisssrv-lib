# zfs_encryption

Boot-time unlock of ZFS-native encrypted pools on bare-metal Proxmox hosts,
fetching each pool's passphrase from a 1Password Connect instance.

## What it deploys

Per host:

- `/etc/onepassword-connect/token` (0400 root) — Connect access token, only on
  hosts with a non-empty `zfs_encryption_pools`.
- `/etc/zfs/encryption/pools/<pool>.conf` (0400 root) — env file read by the
  unit: `ZFS_ENCRYPTION_ITEM=<1P item title>` and `ZFS_ENCRYPTION_FIELD=`.
- `/usr/local/sbin/zfs-load-key.sh` (0750) — fetches the passphrase from
  Connect and runs `zfs load-key` on every encryption root under the pool. It
  does **not** mount: a `zfs mount -a` here would race sibling per-pool
  instances and could not propagate out of the unit's `ProtectSystem=strict`
  mount namespace.
- `/etc/systemd/system/zfs-load-key@.service` — per-pool template unit, enabled
  for each entry in `zfs_encryption_pools`.
- `/usr/local/sbin/zfs-mount-encrypted.sh` + `zfs-mount-encrypted.service` —
  the late, retrying mount anchor. Rendered and enabled only where pools are
  configured; removed again if the list is emptied.
- `/usr/local/sbin/zfs-start-encrypted-guests.sh` +
  `pve-start-encrypted-guests.service` — starts the guest cohort once the
  anchor is active. Enabled only where a cohort is configured.

De-listing a pool is reconciled: its env file and its `.wants` symlink are
removed on the next run.

## Boot ordering

The design goal is that the host **always boots to a usable state — ssh and
Tailscale up — whether or not the unlock ever succeeds**. Only the encrypted
data, nfsd's encrypted exports and the gated guests come up late.

- `zfs-load-key@<pool>` is `After=zfs-import.target network-online.target`
  (Connect is reachable only once the network is up) and
  `WantedBy=zfs-mount-encrypted.service`.
- It is **never** `Before=` or `RequiredBy=zfs-mount.service`. That pair closed
  an ordering cycle (`zfs-mount` → `local-fs` → `network-online`) and put a
  network-dependent unlock in the early boot path. Stock `zfs-mount.service`
  still runs early; its `zfs mount -a` simply skips the locked datasets and
  exits 0.
- `WantedBy` (not `RequiredBy`) the anchor: one pool failing to unlock must not
  fail the anchor, the other pools, or any boot target.
- `zfs-mount-encrypted.service` is `WantedBy=multi-user.target`, `Wants=` (not
  `After=`) each key-load, and its `ExecStart` is an
  `until <script>; do sleep 30; done` loop under `TimeoutStartSec=0`. It
  therefore stays **`activating`** — never `failed` — until every encryption
  root reports `keystatus=available`. That is what holds `After=` dependents
  (nfsd, the guest starter) queued instead of releasing them onto locked
  storage: a plain oneshot's *first* failed start completes the start job and
  cascades `Requires=` failures onto export `.mount` units, none of which retry.

### Retry budget

Every retryable path in `zfs-load-key.sh` (waiting for the pool import, then
the vault / item-id / item-field lookups) burns a full
`zfs_encryption_fetch_timeout_seconds` before exiting, so one `ExecStart` is
worst-case ~4× that plus `RestartSec` — far inside `StartLimitBurst=60` per
`StartLimitIntervalSec=3600s`. Those paths retry indefinitely. The one fast
failure is exit 5 (`zfs get encryptionroot` enumeration), which fires right
after a successful fetch and so trips the burst limit in roughly half an hour —
the intended "a real misconfiguration eventually surfaces as `failed`".

### Guest cohort

`pve-start-encrypted-guests.service` gates **only** the VMIDs/CTIDs listed in
`zfs_encryption_guest_vmids` / `_ctids`; it never touches `pve-guests.service`.
Leave out any guest that must start early on its normal onboot path — a
control-plane/etcd member whose quorum must not wait on this host's unlock, for
instance. Guests in the cohort should carry `onboot=0`, since membership here
is what starts them.

## Threat model

Encryption protects against **disk-leaves-building** scenarios: RMA, disposal,
theft of an offline drive. It does not protect a running system — anyone with
root on the live host can read the Connect token and fetch the same passphrase.
Storing the key on disk would have the same exposure with one fewer
indirection.

The token is narrower than a 1Password Service Account token: it reads only the
configured Connect server's vault, and only where that Connect endpoint is
reachable — keep it on an internal-only ingress.

**Changing `zfs_encryption_token_path` orphans the old token.** The role's
fail-closed cleanup — emptying `zfs_encryption_pools`, or setting
`zfs_encryption_key_command` — removes the token at the **currently configured**
path, which is the only path it knows. Repoint the variable and the credential
stays behind at the previous location, readable by root, outliving every
rotation the role performs and invisible to `--check`. Delete the old file by
hand in the same change (and rotate the token if it sat somewhere it should not
have).

Every pool left out of `zfs_encryption_pools` is unencrypted at rest, including
any pool holding VM root disks and any swap that is not itself a crypt device.
Treat that as a deliberate residual with a drive-wipe SOP as the compensating
control, or encrypt it.

## Cold-cluster boot

If everything power-cycles together and Connect is not up yet, the per-pool
units retry continuously and the anchor stays `activating`, so nfsd and the
gated guests wait rather than fail. When Connect appears, every layer converges
on its own: keys load → datasets mount → anchor active → nfsd → guests. No
operator action required.

If Connect never comes up (or a passphrase was rotated), unlock by hand and the
retrying units close the loop:

```bash
ssh <host>                 # always reachable: sshd/tailscaled are on the
                           # unencrypted root, with no ZFS ordering edges
sudo zfs load-key <pool>   # paste the passphrase from the 1P mobile app
# Nothing else is required — the anchor mounts on its next 30s retry. To skip
# the wait:
sudo systemctl start zfs-mount-encrypted.service pve-start-encrypted-guests.service
```

There is no `RequiredBy=zfs-mount.service` edge, so `zfs-mount.service` does not
go `failed` on a locked boot and needs no `reset-failed`.

## Variables

| Variable | Required | Notes |
|----------|----------|-------|
| `zfs_encryption_pools` | yes (to do anything) | List of `{name, item, field}`. Empty = deploy the scripts + template unit only: no token, no enabled units, no mount anchor. |
| `zfs_encryption_connect_token` | yes, where pools are set | Injected at runtime from 1Password; asserted non-empty. |
| `zfs_encryption_connect_url` | yes, where pools are set | Defaults to `https://connect.<zfs_encryption_internal_domain>`; asserted non-empty. |
| `zfs_encryption_internal_domain` | no | Aliases the inventory-wide `internal_domain`. |
| `zfs_encryption_connect_vault` | no | Vault holding the passphrase items (`Homelab`). A 26-char lowercase id is used as a vault UUID directly; anything else is resolved by name at runtime. Scope it to a vault holding only the passphrases — see below. |
| `zfs_encryption_guest_vmids` / `_ctids` | no | Guest cohort started after the mount anchor. |
| `zfs_encryption_fetch_timeout_seconds` | no | Per-phase deadline inside one script invocation (120). |
| `zfs_encryption_fetch_retry_seconds` | no | Sleep between Connect retries (5); jittered. |
| `zfs_encryption_install_zfsutils` | no | Set false only in CI images without `zfsutils-linux`; also skips the pool-is-encrypted assert. |
| `zfs_encryption_token_path` | no | Where the Connect bearer token lands (`/etc/onepassword-connect/token`); its parent directory is created `0700`. **Changing it strands the old file** — see below. |
| `zfs_encryption_key_command` | no | Secrets-backend seam — see below. Empty (default) = 1Password Connect. |

## Using a secrets backend other than 1Password Connect

`zfs_encryption_key_command` replaces the Connect fetch with any command that
prints the passphrase on stdout. It is a **template-level** switch: with it empty
the rendered `zfs-load-key.sh` is byte-identical to the Connect-only script, and
with it set the Connect code is not rendered at all — no `curl` to an access
point, no token deployed (an existing one is removed), and
`zfs_encryption_connect_url` / `_token` are neither required nor asserted.

```yaml
zfs_encryption_key_command: >-
  vault kv get -field="$ZFS_ENCRYPTION_FIELD" "secret/zfs/$ZFS_ENCRYPTION_POOL"
```

The command runs as root from `zfs-load-key@<pool>.service` with
`ZFS_ENCRYPTION_POOL`, `ZFS_ENCRYPTION_ITEM` and `ZFS_ENCRYPTION_FIELD` exported
— item/field are opaque locators, so the same `zfs_encryption_pools` shape
addresses a Vault path or an SOPS key. Everything around the fetch is unchanged:
pool-import wait, the already-unlocked short-circuit, per-encryption-root
`zfs load-key`, and the mount/guest-start ordering units.

Two obligations come with it. The command owns its own retry and timeout
(`zfs_encryption_fetch_*` do not apply to it) — a non-zero exit is reported as
rc 2, which the unit retries, and empty output as rc 3, which stops it. And it
must work at **boot**, before any encrypted dataset is mounted: keep its binary
and credentials on the root filesystem.

## Required 1Password items

One `Password` item per pool in the configured vault, with a field matching
`zfs_encryption_pools[*].field` (default `passphrase`) and a title matching
`zfs_encryption_pools[*].item`. Convention: `ZFS Pool <pool> Passphrase`.

### Scoping the Connect token

The token lands on disk as a plaintext bearer credential (`0400 root`) on every
host with pools, and it can read **every item in every vault the token covers**.
`zfs_encryption_connect_vault` defaults to `Homelab` — the vault name this
collection's original consumer uses — so a deployment that keeps a mixed-purpose
vault gives a boot credential read access to all of it. Point it at a vault
holding only the pool passphrases.

Order matters, because a Connect **token** can only cover vaults the Connect
**server** itself has access to. Minting a token against a vault the server
cannot reach yields one that 403s, and a token that cannot read the item makes
`zfs-load-key@<pool>.service` retry forever and drags `zfs-mount` with it — a
failure that only shows up at the next boot. So: create the vault and move the
passphrase items into it, grant the existing Connect server access to it, mint
the new token, set `zfs_encryption_connect_vault`, converge, prove an actual key
load, and only then revoke the old token.

`zfs_encryption_key_command` sidesteps Connect entirely and is the alternative
when a dedicated vault is not worth those steps.

## Upgrading

Hosts first configured before the mount-anchor layout may still carry
`/etc/systemd/system/zfs-mount.service.requires/zfs-load-key@*.service` symlinks
from the old `RequiredBy=` `[Install]`. The role no longer sweeps them; delete
any that exist (`systemctl disable` will not, it only removes the current link)
and `systemctl daemon-reload`, or the next boot fails `zfs-mount.service`.
