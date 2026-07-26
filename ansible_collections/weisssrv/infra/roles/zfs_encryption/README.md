# zfs_encryption

Boot-time unlock of ZFS-native encrypted pools by fetching the
passphrase from 1Password Connect.

## What it deploys

Per host:

- `/etc/onepassword-connect/token` (mode 0400, root) — Connect access
  token used by the unlock script.
- `/etc/zfs/encryption/pools/<pool>.conf` (mode 0400, root) — env file
  loaded by the systemd unit, containing `ZFS_ENCRYPTION_ITEM=<title>`
  and `ZFS_ENCRYPTION_FIELD=passphrase` for each pool.
- `/usr/local/sbin/zfs-load-key.sh` (mode 0750) — fetches passphrase
  from Connect and runs `zfs load-key`. Mounting is handled by
  `zfs-mount-encrypted.service` (below); calling `zfs mount -a` from
  inside the key-load unit would race across parallel-running pool
  instances and couldn't propagate mounts outside its
  ProtectSystem=strict namespace anyway.
- `/etc/systemd/system/zfs-load-key@.service` — template unit, **off the
  early-boot critical path** (reworked 2026-06). Ordered
  `After=zfs-import.target network-online.target` (+`Wants=`) so it can
  reach Connect, and `WantedBy=zfs-mount-encrypted.service` (best-effort —
  a failed pool unlock no longer fails any mount or boot target). It is
  deliberately NOT `Before=zfs-mount.service` / `RequiredBy=zfs-mount.service`
  anymore: those two edges put a network-dependent unlock in the early
  `local-fs` path and closed an ordering cycle. `Restart=on-failure`,
  `StartLimitIntervalSec=3600s` / `StartLimitBurst=60`; each ExecStart does
  two sequential Connect calls (~240s worst case) so only ~13 attempts fit
  the 1h window — it retries continuously rather than tripping to failed.
- `/usr/local/sbin/zfs-mount-encrypted.sh` + `zfs-mount-encrypted.service`
  — the **late, retrying mount anchor**. `WantedBy=multi-user.target`,
  `After=zfs-mount.service network-online.target`, and `Wants=` (pull, **not**
  `After=`) each `zfs-load-key@<pool>.service` — an `After=` on the key-loads
  would keep this unit queued behind a restarting key-fetch (Connect down)
  instead of running its own keystatus retry. **Never `Before=` any boot
  target**. Its `ExecStart` is an `until <script>; do sleep 30; done` loop
  (with `TimeoutStartSec=0`): each pass mounts the ready pools (`zfs mount -a`,
  skipping still-locked ones) and exits non-zero until every encryption root
  reports `keystatus=available`, so the unit stays **`activating`** (never
  `failed`) until storage is truly ready. That is deliberate — a plain
  oneshot's *first failed* start would complete the start job and **release**
  units ordered `After=` it (nfsd, the guest starter), and cascade `Requires=`
  failures onto the export `.mount` units (none of which retry), so a degraded
  cold boot could leave nfsd/guests down until manual recovery. The loop holds
  those dependents queued until the mount succeeds. (`Restart=on-failure` +
  `StartLimitIntervalSec=0` remain only as a backstop for the bash wrapper
  itself dying.) This is the single anchor that nfsd and the encrypted-storage
  guests order `After=`.
- `/usr/local/sbin/zfs-start-encrypted-guests.sh` +
  `pve-start-encrypted-guests.service` — starts the Proxmox guests whose
  disks live on encrypted pools (`zfs_encryption_guest_vmids` /
  `_ctids`), but only once `zfs-mount-encrypted.service` is active. Gates +
  retries; idempotent (`grep -q running`). Does NOT touch `pve-guests`, so
  ungated guests (e.g. the etcd VM) start early on their normal onboot path.
- `zfs-load-key@<pool>.service` enabled per pool listed in
  `zfs_encryption_pools`; `zfs-mount-encrypted.service` enabled where any
  pool is listed; `pve-start-encrypted-guests.service` enabled where a guest
  cohort is configured. The role also **unconditionally sweeps** the legacy
  `/etc/systemd/system/zfs-mount.service.requires/zfs-load-key@*.service`
  symlinks left by the old `RequiredBy=` form.

## Threat model

Encryption protects against **disk-leaves-building** scenarios: RMA,
disposal, theft of an offline drive. It does NOT protect against
running-system theft — anyone with root on the running host can
extract the Connect token and use it (over LAN) to fetch the same
passphrase. Storing the literal key on disk would have the same
exposure with one fewer indirection.

The Connect token is strictly more limited than a 1Password Service
Account token: it can only read items from the configured Connect
server's vault (`Homelab`), and only over the LAN since
The Connect endpoint must be reachable from the host at boot.

## Cold-cluster boot (boot never hangs)

The key property of the reworked ordering: **the host always boots to a
usable state — ssh + Tailscale up — regardless of whether the unlock ever
succeeds.** Boot reaches `multi-user.target` on the plaintext datasets alone
(stock early `zfs-mount.service` runs `zfs mount -a`, which skips the locked
encrypted datasets and returns 0). Only the *encrypted data*, nfsd's
encrypted exports, and the encrypted-storage guests come up late.

If everything power-cycles together and Connect (in k8s) isn't up yet, the
`zfs-load-key@<pool>.service` units retry continuously, and
`zfs-mount-encrypted.service` stays `activating` (its `until` loop retries every
30s) until keys load — keeping nfsd and the gated guests queued behind it rather
than letting them fail early. When Connect comes up, every layer converges on
its own: keys load → datasets mount → the anchor goes `active` → nfsd starts
ordered after it → the gated guests start. No operator action required.

If Connect never comes up (or a passphrase rotated), an operator
hand-unlocks over Tailscale and the retrying units close the loop
automatically:

```bash
ssh <host>                   # always reachable — sshd/tailscaled are on the
                             # unencrypted root, with no ZFS ordering edges
sudo zfs load-key tank
sudo zfs load-key ssd        # paste passphrases from the 1P mobile app
# nothing else needed: zfs-mount-encrypted.service mounts on its next retry,
# then nfsd + pve-start-encrypted-guests.service converge. To not wait for
# the 30s retry cadence, nudge them:
sudo systemctl start zfs-mount-encrypted.service pve-start-encrypted-guests.service
```

There is no longer a `RequiredBy=zfs-mount.service` edge, so `zfs-mount.service`
does NOT go to a failed state on a locked boot and needs no `reset-failed`.

## Variables

See `defaults/main.yml`. Key ones:

| Variable | Required | Notes |
|----------|----------|-------|
| `zfs_encryption_connect_token` | yes (host with pools) | Provide via `op read` at runtime |
| `zfs_encryption_pools` | yes | List of `{name, item, field}` per pool |
| `zfs_encryption_connect_url` | yes (host with pools) | Derived from `zfs_encryption_internal_domain` as `https://connect.<domain>`; asserted non-empty |
| `zfs_encryption_internal_domain` | no | Aliases the inventory-wide `internal_domain` |
| `zfs_encryption_connect_vault` | no | Defaults to `Homelab` |

### Deliberate changes from the pre-collection role

- `zfs-load-key.sh` clears its `/dev/shm` response file from a single
  `EXIT`/`INT`/`TERM` trap instead of per call site, so an errexit abort or a
  signal between `mktemp` and `rm` cannot leave the passphrase in tmpfs.
- `zfs-mount-encrypted.service` renders `ExecStart=/bin/true` when
  `zfs_encryption_pools` is empty. The mount script exits 1 on its usage guard
  with no arguments, and `TimeoutStartSec=0` would make the `until` loop spin
  forever — a manual `systemctl start` on such a host is now a clean no-op.
  Hosts with pools render exactly as before.

## Required 1Password items

Per pool, create a `Password` item in `Homelab` vault with field
`passphrase`. Title should match `zfs_encryption_pools[*].item`.
Naming convention: `ZFS Pool <pool> Passphrase` (e.g. `ZFS Pool tank
Passphrase`).
