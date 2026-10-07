# weisssrv.infra.encrypted_swap

**dm-crypt plain-mode swap with a random, ephemeral key** for bare-metal hosts.
A fresh key is drawn from `/dev/urandom` at every boot and discarded at
shutdown, so on-disk swap is **unrecoverable after a reboot** — no passphrase,
no key material to manage, no unlock step. Closes the "secrets paged to
plaintext swap" gap next to at-rest disk encryption.

## Mechanism

- **`/etc/crypttab`** — `cryptswap <source> /dev/urandom
  swap,cipher=aes-xts-plain64,size=512,sector-size=4096`. `systemd-cryptsetup`
  opens the backing device with a random key and `mkswap`s the mapper (the
  `swap` option) at boot. `size=512` is AES-256-XTS; no `luks` option means
  plain mode.
- **Packages** — both `cryptsetup` and `systemd-cryptsetup`. Debian trixie and
  PVE 9 ship the crypttab generator in the second package, and `cryptsetup` does
  not pull it in; without it the unit is never generated and swap never
  activates.
- **`/etc/fstab`** — the encrypted mapper line
  `/dev/mapper/cryptswap none swap sw,pri=100,nofail 0 0`. `nofail` lets
  `swapon -a` skip the mapper while it is still absent; `pri=` makes the kernel
  prefer it for new swap-outs as soon as it comes up.

## Variables

| Variable | Default | Purpose |
|---|---|---|
| `encrypted_swap_enabled` | `true` | Set false to make the role a no-op on a host. |
| `encrypted_swap_source_device` | `/dev/pve/swap` | Backing swap device (the Proxmox installer's LVM layout); override per host if a box differs. |
| `encrypted_swap_require_source_device` | `true` | Fail the deploy when that device is not a block device; `false` self-skips loudly instead. |
| `encrypted_swap_mapper_name` | `cryptswap` | Mapper name; also names the `systemd-cryptsetup@` unit. |
| `encrypted_swap_cipher` | `aes-xts-plain64` | crypttab cipher. |
| `encrypted_swap_key_size` | `512` | Key size in bits (512 ⇒ AES-256-XTS). |
| `encrypted_swap_sector_size` | `4096` | crypttab sector size. |
| `encrypted_swap_mapper_swap_priority` | `100` | fstab `pri=` for the mapper line; must be above the plaintext line's priority. |
| `encrypted_swap_textfile_dir` | `node_exporter_host_textfile_dir`, else `/var/lib/node_exporter` | Where the finalize unit writes `encrypted_swap.prom`. Skipped when the directory is absent. |

## Activation

Converge writes the config, then **reboot the host to activate it**. Existing
plaintext swap keeps running until then, and there is no live switchover: a
running host would need `swapoff` first, and opening the mapper `mkswap`s
through dm-crypt and destroys the plaintext swap header, so a failed live switch
could not roll back.

Expect one degraded boot. On the activation reboot the retained plaintext fstab
line fails to `swapon`, because the mapper has claimed the backing device, so
`systemctl is-system-running` reports `degraded` for that boot. Encrypted swap
is already active over the `nofail` mapper line, and the boot finalize unit
comments the plaintext line out, so later boots are clean.

## Design notes

- The role never leaves a host with zero swap. The plaintext backing line stays
  in fstab next to the higher-priority mapper line until the mapper is live.
- `encrypted-swap-finalize.service` runs once after `swap.target`, `swapoff`s
  the plaintext device and comments its fstab line out. Re-runs and later boots
  are no-ops.
- At boot both the retained plaintext line and `systemd-cryptsetup@cryptswap`
  want the backing device. systemd orders `cryptsetup.target` before
  `swap.target`, so the mapper normally wins.
- The finalize unit handles the rare loss: if the mapper is not active it
  `swapoff`s the plaintext device, opens the mapper and `swapon`s it. Memory is
  empty at boot, so that `swapoff` cannot OOM.
- Opening the mapper `mkswap`s through dm-crypt and destroys the backing
  device's plaintext swap signature, so every restore arm runs `mkswap` before
  `swapon -a`.
- Active-mapper detection resolves `/dev/mapper/cryptswap` to its `/dev/dm-N`
  name before matching `/proc/swaps`: dm devices never appear there by their
  `/dev/mapper/` path.
- The role stats `encrypted_swap_source_device` before writing anything. A
  crypttab entry naming a device the host lacks would leave
  `systemd-cryptsetup@<mapper>` failed on every boot while `nofail` kept the
  host booting.
- The self-skip arm reconciles rather than just declining to write: a host that
  later loses its backing device gets the crypttab entry, the mapper fstab line
  and the enabled finalize unit removed. The plaintext backing line is left
  alone, since removing it is the finalize unit's job.
- Every failure branch of the finalize script exits 0, because the invariant is
  to never leave the host swapless and a restart loop would help nobody. The
  cost is that the unit stays green while the host runs unencrypted swap, so
  the script publishes a textfile gauge on every exit path:

  | Metric | Meaning |
  |---|---|
  | `encrypted_swap_mapper_active` | `1` when the dm-crypt mapper is the active swap device, `0` when the plaintext fallback is. |
  | `encrypted_swap_any_swap_active` | `1` when the host has any swap at all. `0` means the plaintext restore failed and the host is swapless. |
| `encrypted_swap_finalize_last_run_seconds` | Unix time the finalize unit last ran. |

  Alert on `encrypted_swap_mapper_active == 0`: the at-rest control this role
  exists to provide is off. The gauge is absent when
  `encrypted_swap_textfile_dir` does not exist, and every fallback branch also
  logs `daemon.err` under the tag `encrypted-swap`, so a log rule catches the
  host a missing textfile collector would hide. The self-skip arm (backing
  device absent) removes `encrypted_swap.prom`, so a stale `1` cannot outlive
  the encrypted swap it described.
- `nas_storage`'s `swap-clean.sh` is device-agnostic and works against the
  mapper unchanged. It skips its cycle while any fstab swap device is absent,
  which is exactly the pre-reboot window.

## Scope

Bare-metal hosts only — a VM or container has no backing swap LV to encrypt.

## Molecule

Two hosts. The first gets a real backing block device (a loop device published
at `/dev/pve/swap`) and asserts the rendered crypttab, fstab and finalize unit,
then executes the finalize script against a fixture `/proc/swaps` and
`/etc/fstab` with stubbed `swapon`/`swapoff`/`mkswap`/`systemctl`, covering all
three arms: mapper already active, plaintext won the race, and a failed mapper
open.

The second host has no backing device and `encrypted_swap_require_source_device:
false`. Prepare seeds the artifacts an earlier converge would have left; verify
asserts they are reconciled away and that the plaintext backing line survives.
