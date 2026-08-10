# Role: resolv_conf

Shared helper that owns `/etc/resolv.conf`. It is normally reached through
another role rather than invoked directly: `weisssrv.infra.base` (unless DNS
config is skipped) and `weisssrv.infra.adguard_home`, which points the resolver
host at its own instance via `127.0.0.1`.

Writes the nameserver list, an optional `domain` + `search` pair, and a single
combined `options timeout:2 attempts:2` line (musl honours only the first
`options` line, so it is never split).

## Inputs

| Variable | Default | Purpose |
|---|---|---|
| `resolv_conf_nameservers` | `host_dns_servers` (else `[]`) | **Required** — nameserver IPs. The role asserts it is non-empty; the unprefixed `host_dns_servers` is the alias callers already pass |
| `resolv_conf_internal_domain` | `internal_domain` (else `''`) | Only used as the first entry of the search list |
| `resolv_conf_search_domains` | `[resolv_conf_internal_domain]`, `[]` when unset | Search-suffix domains. Set `[]` to omit **both** `domain` and `search` (`domain` is a one-element search list, so suppressing `search` alone still expands suffixes) |
| `resolv_conf_immutable` | `false` | Strip `chattr +i` before the write, re-set it after, and assert it stuck |
| `resolv_conf_is_container` | derived from `ansible_facts['virtualization_type']` | Overridable container detection |
| `resolv_conf_unsafe_writes` | `false` | Direct (non-atomic) write; needed only where the file is bind-mounted and the rename returns `EBUSY`, i.e. Molecule containers |

Set `resolv_conf_search_domains: []` on Kubernetes nodes: kubelet copies the
host's search list into every pod, where the default `ndots:5` makes each
cluster-internal lookup get suffixed and probed upstream first.

`chattr +i` needs `CAP_LINUX_IMMUTABLE` in the owning user namespace, which
unprivileged LXC guests lack. There the role warns instead of failing, and the
file's protection is that it is Ansible-managed and rewritten on every run.
