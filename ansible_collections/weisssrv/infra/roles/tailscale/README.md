# Role: tailscale

Installs Tailscale from the upstream apt repository at a pinned version, joins
the tailnet, and reconciles the node's preferences (route/DNS acceptance,
advertised subnet routes, ACL tags) on every run.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `tailscale_enabled` | `true` | |
| `tailscale_version` | pinned | installed exactly, then `dpkg` held — the upstream repo continuously serves newer builds |
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

The signing key is re-downloaded on every run into a staging path, its primary
fingerprint is matched against `tailscale_gpg_fingerprint`, and only then is it
copied into `/usr/share/keyrings/`. Installing first and verifying after would
leave a tampered download trusted by apt on an established host even though the
play failed.

## IP forwarding

A node advertising routes gets two things, because on a Proxmox host bridge
initialization can reset `ip_forward` **after** systemd-sysctl has run:

1. `/etc/sysctl.d/99-tailscale-ip-forward.conf` — role-owned, deliberately not
   the sysctl module's `/etc/sysctl.conf` default and deliberately not
   `nic_tuning`'s file, which that role deletes on its disable path.
2. a `tailscaled.service` `ExecStartPost` that re-asserts the value.

Removing routes removes both, but does **not** force the live value back to 0:
`nic_tuning` may legitimately own `ip_forward` on the same host, and fighting it
would be worse than a demoted router that keeps forwarding until reboot.

## ACL tags

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
