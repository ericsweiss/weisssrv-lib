# tailscale-acl

Tailnet policy (ACLs, SSH rules, route auto-approvers) plus Split-DNS
nameservers as code.

The module is the **shape**; the policy document itself (`policy.hujson`) is site
data the caller supplies.

## Consuming it

The tag below is an example: use the tag your repo pins (docs/VERSIONING.md).

```hcl
module "tailnet" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/tailscale-acl?ref=v0.7.0"

  acl_policy = file("${path.module}/policy.hujson")

  # REQUIRED — pass {} for a tailnet with no Split-DNS.
  split_dns = {
    "internal.example.com" = { device_hostname = "ts-dns" }
    "lab.example.com"      = { nameservers = ["100.64.0.10"] }
  }
}
```

Provider and backend belong to the root module:

```hcl
provider "tailscale" {
  oauth_client_id     = var.tailscale_oauth_client_id
  oauth_client_secret = var.tailscale_oauth_client_secret
  # `dns` is only needed when split_dns is non-empty; the OAuth client must be
  # granted the scope in the admin console too (provider scopes are a subset).
  scopes = ["acl", "dns"]
}

terraform {
  backend "http" {} # its own state name — never shared with another module
}
```

## Inputs

| Input | Type | Default | Notes |
|---|---|---|---|
| `acl_policy` | string | — | HuJSON policy document. Read it in the **root** module: `path.module` inside this module resolves to the module directory, not the caller's. |
| `split_dns` | map(object) | **none — required** | Domain → `{ nameservers = [...] }` or `{ device_hostname = "..." }`. Exactly one per entry. |

## Outputs

`acl_id`, `split_dns_nameservers` (domain → resolved nameserver IPs, with device
hostnames already looked up).

## Why `split_dns` has no default

A gated, defaulted-off Split-DNS variable (`enable_split_dns = false` + `count`)
is a trap: once the entry exists, every plan or apply that does not set the flag
computes `count = 0` and **plans a destroy of live production DNS** — and a
scheduled drift-plan job silently shows the same destroy, masking real drift.
`prevent_destroy` does not save you, because the removal comes from the
expression, not from a `terraform destroy`.

Making the input required removes the failure mode: an unset value is a hard
error, plan and apply always see the same value, and staging is a code change
rather than an environment variable. The two-phase rollout still works —

1. **Phase A**: `split_dns = {}`, apply the ACL alone.
2. **Phase B**: once the resolver device is registered
   (`tailscale status` shows it), add its entry and apply again. Adopt a
   console-created entry first with
   `terraform import 'module.tailnet.tailscale_dns_split_nameservers.this["internal.example.com"]' internal.example.com`.

Resolving a nameserver by `device_hostname` derives the 100.x address at plan
time, so a rebuilt device self-heals instead of leaving a stale literal. The
module picks the device's single non-IPv6 tailnet address rather than
`addresses[0]` — the API's ordering is convention, not contract, and an IPv6
nameserver here breaks resolution for every tailnet client of the domain. A
device that exposes no IPv4, or more than one, fails the plan with a
precondition naming the domain and the device instead of programming a bad
nameserver; pass `nameservers` explicitly for such a device. The lookup waits up
to 60s for the device to appear; that duration is a literal rather than an input
because the provider parses it during `terraform validate`, where a variable
reference is still unknown.

## Apply is supervised

A bad ACL can sever tailnet and SSH access to every node. Apply it
interactively, never with `-auto-approve`, and keep a non-tailnet path (console
access or a LAN session) open until the post-apply checks pass.

Three guardrails are hardcoded, and none is an input — `lifecycle` blocks cannot
take variables, so unlike `cloudflare-zone`'s per-record `protected` flag there
is no per-consumer switch to route. Two are on the ACL resource:

- `reset_acl_on_destroy = false` — destroying the resource must not revert the
  tailnet to the default allow-all policy. That is a silent security
  regression, not a rollback.
- `lifecycle { prevent_destroy = true }` — any plan that would destroy the ACL
  resource errors out instead.

The third is on `tailscale_dns_split_nameservers.this`: it also carries
`prevent_destroy = true`, because destroying a Split-DNS entry silences that
domain for the whole tailnet.

Because the module is sourced at a pinned `?ref=`, a consumer **cannot** remove
that block. To stop managing the ACL, drop it from state and then delete the
module block:

```bash
terraform state rm 'module.<name>.tailscale_acl.this'
```

The live tailnet policy is left exactly as it is — `reset_acl_on_destroy =
false` means a destroy would not have reverted it either, so this loses nothing
but the state entry. Re-adopt it later with `terraform import`. The only other
path is vendoring the module source into the consumer repo and editing it
there.

Removing an entry from `split_dns` is **not** a plain code edit either — the
`prevent_destroy` above turns it into a hard plan error. Drop it from state
first, then delete the key:

```bash
terraform state rm 'module.<name>.tailscale_dns_split_nameservers.this["internal.example.com"]'
```

The live Split-DNS mapping survives that, so resolution keeps working; delete
the entry in the Tailscale admin console as well if the mapping should really go
away.
