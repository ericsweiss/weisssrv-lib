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
  `swap` option) at boot. `size=512` = **AES-256-XTS** (two 256-bit keys); no
  `luks` option ⇒ plain mode.
  The role installs **both** `cryptsetup` (userspace tools) and
  `systemd-cryptsetup` — Debian trixie / PVE 9 ship the crypttab generator +
  `systemd-cryptsetup@.service` template in the separate `systemd-cryptsetup`
  package, which `cryptsetup` does not pull in; without it the boot generator
  never materializes the unit and swap never activates.
- **`/etc/fstab`** — the encrypted mapper line
  `/dev/mapper/cryptswap none swap sw,pri=100,nofail 0 0`. `nofail` lets
  `swapon -a` silently **skip** the mapper while it is still absent (the
  pre-reboot deferred-activation window) instead of erroring; `pri=` makes the
  kernel prefer the encrypted mapper for new swap-outs the moment it comes up.

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

### Never a zero-swap window

The role **never** produces a state where `swapon -a` yields zero swap. The
plaintext backing line (`<source> none swap …`) is **kept** in fstab *alongside*
the higher-priority mapper line. Until the activation reboot the mapper is absent
(`nofail` ⇒ skipped) and the plaintext device carries swap. A one-shot **boot
finalize unit** (`encrypted-swap-finalize.service`,
`ConditionPathExists=/etc/crypttab`,
`After=swap.target systemd-cryptsetup@cryptswap.service`) then `swapoff`s the
plaintext device and comments its fstab line **exactly once** after the mapper
has come up — so after the activation reboot **only encrypted swap remains active
and fstab is clean**. Idempotent: re-runs and later boots are no-ops.

## Activation is deferred to reboot (no live switch)

Encrypted swap activates on the **next host reboot** — systemd-cryptsetup opens
and `mkswap`s the mapper from the crypttab entry, and the `nofail` fstab mapper
line swaps it on. The config is written idempotently; **reboot to activate**
(hosts under a coordinated reboot controller activate on their next cycle;
reboot the rest when convenient). Existing plaintext swap keeps running until
then.

There is deliberately **no live (running-host) switchover**. Converting an
*active* swap device to encrypted needs a `swapoff` first, and
systemd-cryptsetup's `mkswap` on the mapper **overwrites the plaintext device's
swap header** — so a live switch that fails its post-`swapon` verification cannot
roll back and leaves the host **swapless**. Swap is ephemeral, so deferring to
the clean, standard reboot path costs nothing.

### Boot-race recovery

At boot both contenders want the same backing device: the retained plaintext
fstab line and `systemd-cryptsetup@cryptswap`. systemd orders `cryptsetup.target`
before `swap.target`, so the mapper normally wins; the finalize unit
(`After=swap.target`) handles the rare loss — if the mapper is **not** active it
`swapoff`s the plaintext device, opens the mapper, and `swapon`s it (memory is
empty at boot, so the `swapoff` cannot OOM). Every failure arm restores a working
swap: because opening the mapper `mkswap`s *through* dm-crypt and destroys the
backing device's plaintext swap signature, the restore path runs `mkswap` on the
backing device before `swapon -a` — a bare `swapon -a` would find no signature
and leave the host swapless. Active-mapper detection resolves
`/dev/mapper/cryptswap` to its `/dev/dm-N` kernel name before matching
`/proc/swaps` (dm devices never appear there by their `/dev/mapper/` path).

Expect one benign, self-healing effect around the **activation reboot**: the
retained plaintext line (no `nofail`) fails to `swapon` once the mapper has
claimed the backing device, so its `.swap` unit enters `failed` and
`systemctl is-system-running` reports `degraded` **for that one boot** — until
the finalize unit comments the plaintext line out. Encrypted swap is already
active (the `nofail` mapper line comes up independently), so boot is not harmed;
subsequent boots are clean.

### Interaction with `nas_storage`'s swap-clean

`swap-clean.sh` is **device-agnostic** — it reads swap usage from `/proc/meminfo`
and cycles swap with `swapoff -a` / `swapon -a`, so post-reboot it works
transparently against `/dev/mapper/cryptswap`. The deferred window is guarded on
both sides: swap-clean's pre-flight skips the whole cycle when any fstab swap
device is absent (exactly that window), and the kept plaintext fstab line means
`swapon -a` restores real swap even if it did cycle.

## Backing-device guard

The role is opt-**out** (`encrypted_swap_enabled: true`) with a
Proxmox-installer-specific default source device, so before writing anything it
stats `encrypted_swap_source_device`. A crypttab entry naming a device the host
does not have would leave `systemd-cryptsetup@<mapper>` failed on every boot
while the `nofail` fstab line kept the host booting — quiet, permanent
degradation. With the guard, that host either fails the deploy
(`encrypted_swap_require_source_device: true`, the default) or self-skips with a
loud message.

The self-skip arm **reconciles**, it does not merely decline to write: a host
that converged successfully and later lost its backing device also gets its
crypttab entry and its `/dev/mapper/<mapper>` fstab line removed and the boot
finalize unit disabled, so the every-boot `systemd-cryptsetup@<mapper>` failure
is not carried forward. The plaintext backing fstab line is left untouched —
removing it is the finalize unit's job and only once the mapper is live.

## Scope

Bare-metal hosts only — a VM or container has no backing swap LV to encrypt.

## Molecule

Two hosts. The first is given a real backing block device (a loop device
published at `/dev/pve/swap`, which the role stats with `follow: true`) — without
one the backing-device guard aborts converge and nothing below is exercised.

Activation is deferred to reboot (real devices), so converge asserts the rendered
config: `cryptsetup` **and `systemd-cryptsetup`** installed; the crypttab entry
(mapper, source, `/dev/urandom`, plain-mode cipher/size); the fstab **keeps** the
plaintext line alongside the higher-priority `nofail` mapper line; the obsolete
live-switch script is absent; and the boot finalize unit + script are deployed
and enabled. The finalize-script assertions cover the active-swap gate, the
`readlink -f` resolution to `/dev/dm-N`, the boot-race recovery arm, and the
`mkswap`-before-`swapon -a` restore.

The script is then **executed** against a fixture `/proc/swaps` and `/etc/fstab`
with stubbed `swapon`/`swapoff`/`mkswap`/`systemctl`, asserting the decisions in
all three arms: mapper already active (comment the plaintext line, idempotently,
without entering recovery); plaintext won the race and every step succeeds
(swapoff → open → swapon, then comment); and a failed mapper open (restore with
`mkswap` + `swapon -a`, plaintext line left uncommented). Inverted control flow
passes the string greps but fails these.

The second host has no backing device and `encrypted_swap_require_source_device:
false`. Prepare seeds the crypttab entry, mapper fstab line and enabled finalize
unit an earlier converge would have left; verify asserts all three are gone and
that the plaintext backing line survives.
