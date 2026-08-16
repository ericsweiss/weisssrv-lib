# vfio_passthrough

Host-side codification of GPU **VFIO passthrough** on a Proxmox host.

**OFF by default** — every task self-gates on `vfio_passthrough_enabled`, so the
role is a clean no-op on every host except the one the consumer turns it on for.
It **stages** config and **prints a reboot-required warning**; it **never
reboots** — the operator applies it in a maintenance window.

## What it does (only when `vfio_passthrough_enabled`)

1. Writes `/etc/default/grub.d/vfio-iommu.cfg` — a GRUB drop-in that appends the
   IOMMU kernel params (`intel_iommu=on iommu=pt`) **and** `vfio-pci.ids=<functions>`
   without editing the main grub file → notifies **Update GRUB for VFIO**. The
   cmdline `ids=` is the *primary, earliest* bind — vfio-pci grabs the functions
   the instant it loads in the initramfs, ahead of any host driver.
2. Writes `/etc/modprobe.d/vfio.conf` — `options vfio-pci ids=<functions>` (the
   redundant modprobe.d twin of the cmdline bind), plus `blacklist <mod>` for each
   hard-blacklisted VGA driver (default `nouveau`) and `softdep <mod> pre: vfio-pci`
   for each host driver that *does* load at boot but must yield its function
   (default `snd_hda_intel`, `xhci_hcd`) → notifies **Rebuild initramfs for VFIO**.
3. Writes `/etc/modules-load.d/vfio-pci.conf` — force-loads `vfio-pci` at real-root
   boot as a belt-and-suspenders fallback to the cmdline bind, in case nothing else
   pulls it in (a blacklisted `nouveau` never loads, so its softdep can't) →
   notifies **Rebuild initramfs for VFIO**.
4. Prints **reboot-required** (a `debug` handler, fired only on a real change).
   Never reboots.

## Binding mechanisms

The `vfio-pci.ids=` kernel cmdline (step 1) is the primary bind: vfio-pci claims
every listed function as soon as it loads in the initramfs. The modprobe.d
`ids=`/`blacklist`/`softdep` (step 2, baked into the initramfs) and the
modules-load.d force-load (step 3) are belt-and-suspenders — they keep the host
drivers off the card and guarantee vfio-pci loads even if the coldplug order
changes. Together they make vfio-pci claim all of a multifunction card's
functions (on an NVIDIA GPU: VGA + HD-audio + USB-C xHCI + UART) after a host
reboot. PVE would also bind vfio-pci at `qm start`, but boot-time binding keeps
the host drivers off the card cleanly.

This is the one statement of the argument — the templates and `defaults` point
here rather than restating it.

## Variables

| Variable | Default | Purpose |
|---|---|---|
| `vfio_passthrough_enabled` | `false` | Master gate. Set true on the GPU host only. |
| `vfio_passthrough_pci_ids` | `[]` | `vendor:device` IDs bound to vfio-pci (REQUIRED when enabled). List every function of a multifunction GPU. |
| `vfio_passthrough_blacklist_modules` | `[nouveau, i2c_nvidia_gpu]` | Drivers HARD-blacklisted (never load; no softdep — it would be dead): the nouveau VGA driver, plus `i2c_nvidia_gpu`, which claims the card's UCSI/USB-C function. |
| `vfio_passthrough_softdep_modules` | `[snd_hda_intel, xhci_hcd]` | Host drivers that load at boot but must yield their function → `softdep … pre: vfio-pci`. |
| `vfio_passthrough_force_load` | `true` | Write `/etc/modules-load.d/vfio-pci.conf` to force-load vfio-pci at boot (belt-and-suspenders fallback to the cmdline `vfio-pci.ids=`). |
| `vfio_passthrough_cmdline_params` | `[intel_iommu=on, iommu=pt]` | IOMMU cmdline params appended via the GRUB drop-in. The role *also* appends `vfio-pci.ids=<vfio_passthrough_pci_ids>` (from the template) for the earliest bind — don't add an `ids=` here. |
| `vfio_passthrough_skip_boot_update` | `false` | Molecule/check-mode: render the files, skip `update-grub`/`update-initramfs`. |

## Assumptions & scope

- **GRUB-managed host** — the cmdline is appended via `/etc/default/grub.d/`, so
  the host must boot through GRUB (e.g. an LVM root). A systemd-boot /
  proxmox-boot-tool host keeps its cmdline in `/etc/kernel/cmdline` and would
  need different handling (out of scope).
- The role **never** reboots and **never** does a live driver unbind — capturing
  the GPU is done cleanly on the next boot. Applies in a maintenance window.

## Where it runs

Compose it into the play that covers the Proxmox hosts: every task self-gates on
`vfio_passthrough_enabled`, so only the host the consumer enables acts. The apply
(reboot) is always operator-driven.

Comment-only edits to the three templates still notify the GRUB/initramfs
rebuild handlers and the reboot-required warning, so a release that rewords them
regenerates the boot artifacts once on an enabled host — the binding itself is
unchanged.
