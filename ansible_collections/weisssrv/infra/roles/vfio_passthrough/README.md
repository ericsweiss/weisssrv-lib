# weisssrv.infra.vfio_passthrough

Host-side codification of GPU **VFIO passthrough** on a Proxmox host.

**OFF by default**, and reconciled in both directions: with the gate on the role
stages the config, with it off the role removes the three files again, so a host
that is no longer a passthrough host releases the card on its next boot. It
**stages** config and **prints a reboot-required warning**; it **never
reboots** — the operator applies it in a maintenance window. Compose it into the
play unconditionally, or a host turned off never gets reconciled off.

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
| `vfio_passthrough_manage_absent` | `true` | Lets the disabled arm remove the three VFIO drop-ins. Set `false` to leave every drop-in alone. |
| `vfio_passthrough_managed_marker` | `managed by weisssrv.infra.vfio_passthrough` | Role-owned literal the three templates emit. It identifies a role-written drop-in, so a file without it is reported and kept. |
| `vfio_passthrough_legacy_markers` | the stable prefix of `ansible_managed` | Extra ownership markers the disabled arm accepts, so a drop-in written before the literal above shipped is still removable. An empty entry is ignored. The disabled arm asserts that at least one marker is non-empty. |
| `vfio_passthrough_cmdline_method` | `grub` | How the kernel cmdline is written. `grub` (a `/etc/default/grub.d` drop-in) is the only method implemented; a preflight refuses a host that carries `/etc/kernel/cmdline`. |
| `vfio_passthrough_skip_boot_update` | `false` | Molecule/check-mode: render the files, skip `update-grub`/`update-initramfs`. |

## Assumptions & scope

- **GRUB-managed host** — the cmdline is appended via `/etc/default/grub.d/`, so
  the host must boot through GRUB (an LVM root, or proxmox-boot-tool in GRUB
  mode, where the handler also refreshes the ESP copies). A host that keeps its
  cmdline in `/etc/kernel/cmdline` (systemd-boot) is refused by a preflight
  rather than silently staging parameters the kernel never reads; implementing
  it means a second `vfio_passthrough_cmdline_method`.
- With the gate off the role removes `/etc/default/grub.d/vfio-iommu.cfg`,
  `/etc/modprobe.d/vfio.conf` and `/etc/modules-load.d/vfio-pci.conf`, but only
  where the file carries `vfio_passthrough_managed_marker` or one of
  `vfio_passthrough_legacy_markers`. A host that configured VFIO by hand keeps
  its files and the run reports each one it left alone. Removal rebuilds the
  boot artefacts.
- The role **never** reboots and **never** does a live driver unbind — capturing
  the GPU is done cleanly on the next boot. Applies in a maintenance window.

## Before the first deploy

Confirm no host in the play boots in systemd-boot mode
(`test -f /etc/kernel/cmdline` over the group). One that does fails the
preflight, which is the intended outcome, but finding it in a deploy run costs a
maintenance window.

## Where it runs

Compose it into the play that covers the Proxmox hosts, unconditionally. The
role acts in both directions: with the gate on it stages the three files, with
it off it removes them and the host releases the card on its next boot. The
apply (reboot) is always operator-driven.

Comment-only edits to the three templates still notify the GRUB/initramfs
rebuild handlers and the reboot-required warning, so a release that rewords them
regenerates the boot artifacts once on an enabled host — the binding itself is
unchanged.

## Molecule

`molecule test -s default` from the role directory (CI runs the same scenario).
It renders the three files with the boot-artifact rebuilds suppressed, asserts
their exact `ids=` / `blacklist` / `softdep` / IOMMU cmdline content, and then
asserts that a run with the gate off removes all three.
