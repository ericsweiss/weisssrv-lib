# weisssrv.infra.nic_tuning

Per-NIC tuning for Proxmox (and any Debian host) where a persistent
`ethtool -K` setting, a sticky `ip_forward=1`, a lowered `vm.swappiness` or the
active-backup bond MAC-flap guard is needed.

## Why each knob exists

- **`ethtool -K` drop-ins.** Some drivers wedge with an offload enabled — an
  Aquantia AQC113 deadlocks its receive path with GRO on a bridged interface,
  and the onboard Intel e1000e hits a TX "Hardware Unit Hang" with tso/gso/gro
  on. The fix is per-NIC and must survive reboot, so it is applied live *and*
  written as an `ifup` drop-in. The role owns the whole
  `99-nic-*-tuning.cfg` glob: a drop-in for an interface no longer in
  `nic_tuning_overrides` is removed, including when the list is emptied.
- **`ip_forward` drop-in.** Proxmox's `pve-firewall` can reset
  `net.ipv4.ip_forward`, which breaks overlay-VPN subnet routing. A `sysctl.d`
  drop-in keeps the value sticky.
- **`vm.swappiness` drop-in.** A memory-committed virtualization host (guests
  plus ZFS ARC) thrashes swap at the kernel default of 60.
- **Bond guard.** `bond-mode active-backup` with both legs on an *unmanaged*
  switch plus `all_slaves_active=1` makes the driver deliver frames received on
  the inactive backup leg; the switch floods a guest's own frames back onto that
  leg, the host bridge learns the guest MAC on `bond0` instead of its veth, and
  the guest's return traffic is misdirected out to the switch — an intermittent
  MAC-flapping black-hole that recurs across reboots and HA moves.

## Variables

- `nic_tuning_ip_forward` (default `false`) — write
  `/etc/sysctl.d/99-nic-tuning-ip-forward.conf` with `net.ipv4.ip_forward=1`
  and apply it via a scoped `ansible.posix.sysctl` reload of just
  `net.ipv4.ip_forward` from that drop-in (deliberately not `sysctl --system`,
  so an unrelated bad entry elsewhere in `/etc/sysctl.d/` can't fail the apply).
- `nic_tuning_vm_swappiness` (default `null`) — integer `0`-`100` to write
  `/etc/sysctl.d/99-nic-tuning-swappiness.conf` and apply it with the same
  scoped reload. `null` leaves swappiness unmanaged.
- `nic_tuning_overrides` (default `[]`) — list of per-interface dicts:
  ```yaml
  nic_tuning_overrides:
    - interface: nic1
      options:
        - feature: gro
          value: "off"
  ```
  Writes `/etc/network/interfaces.d/99-nic-<iface>-tuning.cfg` — an
  `iface <iface> inet manual` stanza with a `post-up /sbin/ethtool -K ...`
  line per option — and applies each override immediately. The stanza header is
  load-bearing: ifupdown2 rejects bare `post-up` lines ("error processing
  line"), which would leave the drop-in inert at boot.

  The live apply is **compare-then-set**: the task diffs `ethtool -k` around the
  change so `changed` reflects a real kernel transition. The apply itself does
  not fail the play — the read-back below owns that.
- `nic_tuning_verify_offloads` (default `true`) — after applying, re-read
  `ethtool --show-features` for every overridden interface and **fail the play**
  when a requested value is not live. This is the gate that catches the silent
  modes: a driver that accepts and ignores the request, a renamed interface, a
  feature the driver refuses. Without it the role can go green with the offload
  still on, because the boot-time drop-in's only failure signal is a syslog
  line. Set `false` only in a container test where the ethtool feature ioctls do
  not exist.
- `nic_tuning_feature_names` (default: the standard `ethtool` table) — maps the
  short names `ethtool -K` takes (`gro`, `tso`, …) to the long names
  `ethtool --show-features` prints, which is what the read-back compares. A
  short name used in `nic_tuning_overrides` with no entry here fails the assert
  with exactly that message. Extend it for a feature the table does not cover.

  Adding an override for an unfamiliar NIC/driver adds a case this gate has
  never seen — read the feature back by hand first
  (`ethtool --show-features <iface>`) and confirm the long name appears with the
  value you are asking for, rather than discovering it as a red fleet deploy.
- `nic_tuning_bond_asa_guard` (default `true`) — force `all_slaves_active=0` on
  every `active-backup` bond, across three layers:
  - **`/etc/modprobe.d/bonding.conf`** module option — the *real* boot-time
    control. The bonding module default is applied when the module loads (before
    ifupdown2 runs); a stale `all_slaves_active=1` here is why the guard reverts
    on every reboot. Surgically flips `=1` → `=0`, preserving `fail_over_mac`;
    only touches an existing file.
  - **`/etc/network/interfaces`** stanza — surgical `replace` of
    `bond-all_slaves_active 1` → `0` (never inserts a line, never reloads).
    Belt-and-suspenders: ifupdown2 does **not** honor this stanza.
  - **live sysfs** `/sys/class/net/<bond>/bonding/all_slaves_active` — applies
    the fix now, without a reboot.

  Idempotent and a no-op on non-bonded hosts. Set `false` only if a bond
  legitimately needs `=1` (multi-switch multicast RX).

- `nic_tuning_bond_primary` (default `null`) — name the leg an `active-backup`
  bond should prefer, and `nic_tuning_bond_primary_reselect` (default
  `failure`) the policy for going back to it. `bond-miimon` only watches
  carrier, so a NIC whose transmit unit wedges with the link up still looks
  healthy and the bond never fails over. Naming the other leg as primary makes
  the defect-prone one backup-only, which turns that failure into a
  link-detected one. Applied in two layers:
  - **`/etc/network/interfaces`** stanza — `bond-primary` and
    `bond-primary_reselect` inserted after the `bond-mode active-backup` line,
    which ifupdown2 honors for these two attributes.
  - **live sysfs** `/sys/class/net/<bond>/bonding/primary` — applies now.
    `primary_reselect` is written first: under the kernel default (`always`) a
    primary write moves the active slave immediately and blips the uplink.

  Left `null`, empty or whitespace-only the role reconciles the pin away: it
  removes both lines from `/etc/network/interfaces` and clears
  `bonding/primary` live, so the kernel default `primary_reselect` ("always")
  applies again. A host that pinned a leg by hand outside Ansible either names
  that leg here, or sets `nic_tuning_bond_primary_manage_absent: false`
  (default `true`) to leave the hand-written pin alone.
  Set, the role fails the play rather than pin nothing:

  - `nic_tuning_bond_primary` must name an interface. A value that is not an
    interface name fails the play instead of writing an unusable
    `bond-primary` line.
  - `nic_tuning_bond_primary_reselect` must be `always`, `better` or `failure`.
  - `/etc/network/interfaces` must exist, and hold exactly one
    `bond-mode active-backup` stanza to anchor on. A host configured through
    netplan, systemd-networkd or NetworkManager pins the leg there instead.
  - The named interface must be a slave of the active-backup bond: the stanza's
    `bond-slaves` list, and the live bond where the host has bonding. Both are
    read before either line is written, so a bad name persists nothing.

- `nic_tuning_disable_ipv6` (default `[]`) — list of interfaces to fully disable
  IPv6 on, removing their `fe80::` link-local. Use where an interface's UNTAGGED
  link-local is an unfiltered L2 path the IPv4 VLAN firewall never sees (e.g. a
  NAS uplink whose native VLAN reaches a segment the zone firewall is meant to
  gate). Writes `/etc/sysctl.d/99-nic-tuning-disable-ipv6.conf` and applies live,
  so the link-local disappears with no reboot. Two details:
  - **Slash-separator keys.** The drop-in writes `net/ipv6/conf/<iface>/…`, not
    `net.ipv6.conf.<iface>.…`, because sysctl maps every `.` in a dotted key to
    `/` — which would mangle a VLAN name like `nic1.10`. The `/` form keeps the
    dot in the interface segment.
  - **Strictly per-interface** — never `net.ipv6.conf.all`, so a sibling VLAN
    subinterface, the management bridge and tailscale keep their IPv6. Removing
    an interface from the list drops the drop-in (reverts on next reboot); use
    `sysctl -w net/ipv6/conf/<iface>/disable_ipv6=0` to revert live.

## Example inventory wiring

```yaml
# host_vars/<nas-host>.yml
nic_tuning_ip_forward: true
nic_tuning_vm_swappiness: 1
nic_tuning_overrides:
  - interface: nic1
    options:
      - feature: gro
        value: "off"
```

```yaml
# group_vars/<hypervisors>.yml — ip_forward only, no NIC overrides
nic_tuning_ip_forward: true
```

## Scope

Does not flash NIC firmware.
