# tailscale-acl

Tailnet policy (ACLs, SSH rules, route auto-approvers) plus Split-DNS
nameservers as code.

The module is the **shape**; the policy document itself (`policy.hujson`) is site
data the caller supplies.

## Consuming it

The tag below is an example: use the tag your repo pins (docs/VERSIONING.md).

```hcl
module "tailnet" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/tailscale-acl?ref=v0.29.1"

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

## `split_dns` is required

The input has no default, so plan and apply always see the same value. Pass
`{}` for no Split-DNS entries. Roll out in two phases:

1. **Phase A**: `split_dns = {}`, apply the ACL alone.
2. **Phase B**: once the resolver device is registered
   (`tailscale status` shows it), add its entry and apply again. Adopt a
   console-created entry first with
   `terraform import 'module.tailnet.tailscale_dns_split_nameservers.this["internal.example.com"]' internal.example.com`.

A nameserver given as `device_hostname` resolves to its 100.x address at plan
time, so a rebuilt device self-heals. The module picks the device's single
non-IPv6 tailnet address, because the API's address ordering is not a contract
and an IPv6 nameserver breaks resolution for every tailnet client of the domain.
A device with no IPv4, or more than one, fails the plan with a precondition
naming the domain and the device. Pass `nameservers` explicitly for such a
device.

The lookup waits up to 60s for the device to appear. The duration is a literal
because the provider parses it during `terraform validate`, where a variable is
still unknown.

## Apply is supervised

A bad ACL can sever tailnet and SSH access to every node. Apply it
interactively, never with `-auto-approve`, and keep a non-tailnet path (console
access or a LAN session) open until the post-apply checks pass.

Three guardrails are hardcoded. `lifecycle` blocks take no variables, so none of
them is an input:

- `reset_acl_on_destroy = false` on the ACL: destroying the resource leaves the
  tailnet policy as it is, never the default allow-all.
- `prevent_destroy = true` on the ACL: any plan that destroys it errors out.
- `prevent_destroy = true` on `tailscale_dns_split_nameservers.this`:
  destroying a Split-DNS entry silences that domain for the whole tailnet.

### Stop managing the ACL

Drop it from state, then delete the module block:

```bash
terraform state rm 'module.<name>.tailscale_acl.this'
```

The live tailnet policy stays exactly as it is. Re-adopt it later with
`terraform import`.

### Remove a Split-DNS entry

Drop it from state first, then delete the key:

```bash
terraform state rm 'module.<name>.tailscale_dns_split_nameservers.this["internal.example.com"]'
```

The live Split-DNS mapping survives that, so resolution keeps working. Delete
the entry in the Tailscale admin console as well if the mapping should go away.

## Tests

```bash
cd terraform/modules/tailscale-acl
terraform init -backend=false
terraform test
```

`tests/validation.tftest.hcl` covers every variable validation, the
device-hostname IPv4 selection and the split-DNS precondition. `terraform
validate` evaluates no caller values, so it runs none of them; the runs are
plan-only against a `mock_provider`, so they need no credentials and create no
state. CI runs the same command through `ci/validate/terraform.yml` with
`test: true`.
