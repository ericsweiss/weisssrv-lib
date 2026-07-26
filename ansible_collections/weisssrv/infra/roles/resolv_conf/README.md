# Role: resolv_conf

Shared helper that owns `/etc/resolv.conf` for hosts that need a managed DNS
config. Used transitively by other roles rather than invoked directly.

Consumed by `weisssrv.infra.base` (unless DNS config is skipped) and by
`weisssrv.infra.adguard_home`, which points the resolver host at its own
instance via `127.0.0.1`.

## Inputs

Required:

- `resolv_conf_nameservers` — list of nameserver IPs; the role asserts it is
  non-empty. Defaults to `host_dns_servers`, the neutral (unprefixed) name that
  is the shared contract between `base`/`adguard_home` and this helper, so a
  caller can keep setting either.

Optional:

- `resolv_conf_internal_domain` — defaults to the inventory-wide
  `internal_domain` (empty if unset). Used only as the first entry of
  `resolv_conf_search_domains`.
- `resolv_conf_search_domains` — list of search-suffix domains. Defaults
  to `[resolv_conf_internal_domain]` (empty when that is unset);
  explicitly set to `[]` to omit BOTH the
  `domain` and `search` lines. (`domain` is functionally a 1-element
  search list, so suppressing only `search` would still apply
  search-suffix behavior via `domain`.) Set `[]` on a Kubernetes node:
  kubelet propagates the host's search domains into every pod, which
  inflates every cluster-internal lookup by `ndots:5`.
- `resolv_conf_unsafe_writes` — defaults to `false`. Set to `true` only
  in Molecule container environments where `/etc/resolv.conf` is
  bind-mounted from the host and atomic rename returns `EBUSY`.
  Production hosts never need this.
- `resolv_conf_immutable` — defaults to `false`. When `true`, the role
  removes the `chattr +i` immutable flag before writing, re-sets it
  afterwards, and verifies it stuck (protects the file from DHCP/systemd
  overwrites). On an unprivileged container `chattr +i` cannot succeed
  (no CAP_LINUX_IMMUTABLE in the owning namespace) — the role warns
  there instead of failing, and protection relies on the file being
  Ansible-managed. Container detection is `resolv_conf_is_container`
  (derived from `ansible_facts['virtualization_type']`, overridable).
