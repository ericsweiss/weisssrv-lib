# Role: tailscale

Installs Tailscale from the upstream apt repository at a pinned version, joins
the tailnet, and reconciles the node's preferences (route/DNS acceptance,
advertised subnet routes, ACL tags) on every run.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `tailscale_enabled` | `true` | Role switch — `false` skips every task (honored by the role itself). |
| `tailscale_version` | pinned | installed exactly, then `dpkg` held — the upstream repo continuously serves newer builds. Override wherever the consumer keeps its version pins; the role default moves only on a collection release. |
| `tailscale_gpg_fingerprint` | upstream primary key | the downloaded key is verified against this **before** it is trusted |
| `tailscale_accept_routes` | `false` | keep `false` on a subnet router, or its own advertised routes loop back |
| `tailscale_accept_dns` | `false` | leave the site's resolvers authoritative |
| `tailscale_advertise_routes` | `[]` | e.g. `["192.168.0.0/24"]`; a non-empty list also turns on IP forwarding |
| `tailscale_advertise_tags` | `[]` | the tag must already exist in the tailnet ACL's `tagOwners` |
| `tailscale_tags_require_adoption` | `false` | see below |
| `tailscale_additional_flags` | `[]` | extra `tailscale up` flags; applied only by the initial join |

The auth key is read from the **`TAILSCALE_AUTH_KEY` environment variable**
(passed to `tailscale up` as `TS_AUTHKEY`), never from a variable — so it never
reaches argv, a fact, or a log.

## Key verification

The signing key is re-downloaded on every run into a staging path, checked
there, and only then copied into `/usr/share/keyrings/`. Installing first and
verifying after would leave a tampered download trusted by apt on an established
host even though the play failed.

The check binds the **complete set** of primary-key fingerprints in the
downloaded file to `tailscale_gpg_fingerprint` (subkeys are ignored). Matching
only the first would let a bundle carrying the pinned key *plus* an appended
primary key pass, and the whole file is installed as trusted.

## IP forwarding

A node advertising routes gets two things, because on a host with bridged
networking (a hypervisor, typically) bridge initialization can reset
`ip_forward` **after** systemd-sysctl has run:

1. `/etc/sysctl.d/99-tailscale-ip-forward.conf` — role-owned, deliberately not
   the sysctl module's `/etc/sysctl.conf` default and deliberately not
   `nic_tuning`'s file, which that role deletes on its disable path.
2. a `tailscaled.service` `ExecStartPost` that re-asserts the value.

An `ip_forward` line in `/etc/sysctl.conf` is removed unconditionally, so the
drop-in is the single owner either way.

Emptying `tailscale_advertise_routes` removes both files, but does **not** force
the live value back to 0: `nic_tuning` may legitimately own `ip_forward` on the
same host, and fighting it would be worse than a demoted router that keeps
forwarding until reboot.

## Subnet router on a bridging host (local-guest reachability)

A route-advertising node ALSO gets `/usr/local/sbin/tailscale-bridge-masq-fix`
plus a `tailscaled.service` `ExecStartPost` drop-in that runs it. Without it, a
subnet router that is also a hypervisor (Proxmox, bridged guests) can forward
tailnet traffic to guests on **other** hosts but not to guests hosted on
**itself**: `net.bridge.bridge-nf-call-iptables=1` makes the packet re-traverse
`nat POSTROUTING` at the guest's fw-bridge, and Tailscale's mark-based masquerade
(`0x40000`) fires a **second** time, rewriting the source to the router's own
tailnet IP so the guest's reply is absorbed by the router. The script installs
one rule — `-m physdev --physdev-is-bridged -m mark --mark 0x40000/0xff0000 -j
ACCEPT`, **above** the `-j ts-postrouting` jump — that skips that second
masquerade for bridged-local delivery so the correct first (LAN) masquerade
survives. It is NAT-table only (it changes source translation, not the FILTER
firewall, so it opens no access) and a no-op on a router with no bridged guests.

Staying **above** `ts-postrouting` is why this is a script, not a one-line
`ExecStartPost`: on restart Tailscale removes and re-inserts its own jump at
position 1, which pushes a pre-existing ACCEPT below it, and a plain
insert-if-missing check would then read the mis-ordered rule as present and never
repair it. The script waits (bounded) for the jump to exist, deletes any stale
copy, then inserts once at position 1 — deterministic on every restart and cold
boot. Emptying `tailscale_advertise_routes` removes the script and drop-in; the
live rule (harmless without marked bridged traffic) clears on reboot.

**Prerequisite:** Tailscale must be in **iptables** netfilter mode, not nftables
— the `ts-postrouting` chain and the `xt_physdev` match are iptables constructs.
Under nftables mode the script's wait times out and its rule does not apply (and
is not needed the same way). The current fleet runs iptables mode.

## ACL tags

Deploy order matters: apply the tailnet ACL (which defines `tagOwners`, and the
tag-based route auto-approver if you use one) **before** running this role with
`tailscale_advertise_tags` set.

`--advertise-tags` is deliberately **not** passed to the initial `tailscale up`.
A tag on the join hard-fails while the live ACL has no `tagOwners` entry yet, or
triggers an interactive reauth that a non-interactive run cannot complete. Tags
are instead adopted by a separate `tailscale set --advertise-tags` reconcile,
and the first transition of a user-owned device to a tag-owned identity still
needs a supervised, interactive reauth (a Tailscale platform behaviour).

Not every CLI release carries `--advertise-tags` on `tailscale set`; the role
detects support first, because otherwise the reconcile fails with a full usage
dump on every deploy while the tags are in fact correct server-side.

`tailscale_tags_require_adoption` picks the failure mode:

- **`false` (default)** — best-effort. A non-zero reconcile does not fail the
  play, which is what keeps an automated pipeline green during the pre-ACL
  window where "needs reauth" is the expected state. The result is always
  printed with `rc` + `stderr`, so a genuine error is visible, not swallowed.
- **`true`** — strict, for the supervised adoption step run *after* the ACL
  defines `tagOwners`. Any non-zero rc fails the play. When the CLI cannot
  reconcile non-interactively, strict mode instead asserts the live tag state
  from `tailscale status`, so it cannot pass silently.

## Subnet-router pattern

Advertising the same LAN prefix from several nodes gives real failover rather
than one host as a single point of failure. Route approval is a tailnet-side
concern: auto-approve the prefix in the ACL's `autoApprovers`, or approve each
advertisement by hand in the admin console.
