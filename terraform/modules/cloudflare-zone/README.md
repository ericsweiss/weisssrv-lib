# cloudflare-zone

Zone-wide Cloudflare settings plus declarative DNS records for one zone, with
per-record destroy protection.

The module is the **shape**; the record inventory is site data the caller
supplies (a cluster instance's `dns.tf` / `terraform.tfvars`).

## Consuming it

The tag below is an example: use the tag your repo pins (docs/VERSIONING.md).

```hcl
module "zone" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/cloudflare-zone?ref=v0.9.7"

  account_id = var.cloudflare_account_id
  zone_name  = var.external_domain

  records = {
    root = {
      name                       = var.external_domain
      type                       = "A"
      content                    = "203.0.113.10" # seed only; DDNS owns it
      proxied                    = true
      ttl                        = 1
      comment                    = "IP updated by the DDNS job"
      protected                  = true
      content_managed_externally = true
    }
    caa_issue_letsencrypt = {
      name        = "@"
      type        = "CAA"
      record_data = { flags = 0, tag = "issue", value = "letsencrypt.org" }
      protected   = true
    }
    spf = {
      name    = "@"
      type    = "TXT"
      content = "v=spf1 ~all"
    }
  }
}
```

The provider and backend are the **root module's** job — a reusable module can
declare neither:

```hcl
provider "cloudflare" {
  api_token = var.cloudflare_api_token # Zone:Read + DNS:Edit (+ Zone Settings:Edit)
}

terraform {
  backend "http" {} # configured via TF_HTTP_* (ci/templates/terraform-http-backend.yml)
}
```

Keep the Terraform token separate from any DNS-only token handed to in-cluster
consumers (cert-manager, external-dns): only this one needs
**Zone Settings:Edit**, so a compromised workload cannot downgrade zone-wide TLS
posture.

## Inputs

| Input | Type | Default | Notes |
|---|---|---|---|
| `account_id` | string | — | 32 hex chars; scopes the zone lookup so a like-named zone in another account cannot be picked up. |
| `zone_name` | string | — | Bare FQDN. |
| `manage_zone_settings` | bool | `true` | `false` skips the settings override (DNS-only token). Flipping `true → false` on a managed zone is a plan error — the override carries `prevent_destroy`; see below. |
| `zone_settings` | object | hardened baseline | `ssl=strict`, `always_use_https=on`, `min_tls_version=1.2`, `tls_1_3=on`, `http3=on`, `brotli=on`, `cache_level=aggressive`, `browser_cache_ttl=14400`, HSTS on for 1 year with subdomains, `preload=false`. Enum values (`ssl`, `min_tls_version`, `cache_level`, `tls_1_3`, the on/off toggles) are validated at plan time — Cloudflare rejects a bad one mid-apply. The two numeric settings are bounded instead: `browser_cache_ttl` to 0-31536000, and `hsts.max_age` to > 0 while `hsts.enabled` (0 is how HSTS is withdrawn), rising to >= 31536000 with `include_subdomains` when `preload = true` — the browser preload lists refuse anything shorter. |
| `records` | map(object) | `{}` | See below. |

Each `records` entry: `name`, `type` (A/AAAA/CNAME/TXT/MX/NS/CAA), exactly one
of `content` or `record_data` (`{flags, tag, value}` for CAA), plus optional
`priority` (required for MX), `proxied`, `ttl` (must be `1` when proxied),
`comment`, and the two lifecycle flags below.

## Outputs

`zone_id`, `zone_name`, `zone_status`, `name_servers`, `record_ids`,
`record_hostnames` (the last two keyed by `records` key across every lifecycle
class).

## Protecting catastrophic records

`lifecycle` blocks cannot take variables, so protection cannot be a per-record
argument on one resource. The module instead routes each record to one of four
resources by its flags:

| `protected` | `content_managed_externally` | Resource address | Lifecycle |
|---|---|---|---|
| false | false | `cloudflare_record.this` | none |
| true | false | `cloudflare_record.protected` | `prevent_destroy` |
| false | true | `cloudflare_record.external_content` | `ignore_changes = [content]` |
| true | true | `cloudflare_record.protected_external_content` | both |

- **`protected = true`** for anything whose deletion is an outage or a security
  regression: the zone apex, any hostname carrying SSH or a VPN endpoint, the
  DNS-only record other CNAMEs point at, and the CAA set (losing CAA lets *any*
  CA issue for the domain). Deleting the map entry then fails loudly instead of
  silently applying.
- **`content_managed_externally = true`** where a DDNS job or another controller
  owns the value: Terraform seeds the record once and stops diffing `content`
  (`proxied`/`ttl`/`comment` stay Terraform-owned).
- **Changing either flag changes the resource address**, so Terraform plans a
  destroy + create. Add a `moved {}` block for the change (and if the record is
  moving *out of* `protected`, remove the entry from the protected map in a
  separate applied step first — `prevent_destroy` blocks the destroy half).

`prevent_destroy` covers the records you remembered to flag. Nothing covers the
ones you did not: a pipeline that applies this module unattended is the place to
add a delete-count gate over `terraform show -json` (fail when the plan deletes
anything unless an override variable is set), and neither this library nor its
consumers ship one today — the shared CI templates validate but never plan.

## Turning zone settings off

`cloudflare_zone_settings_override.this` is `count`-driven and carries
`prevent_destroy`, and Terraform rejects *any* plan that destroys a protected
instance — including one produced by driving `count` to 0. So
`manage_zone_settings = false` on a zone that is already managed errors instead
of reverting anything. Drop the override from state first, then flip the
variable:

```bash
terraform state rm 'module.<name>.cloudflare_zone_settings_override.this[0]'
```

The live zone keeps whatever settings were last applied; only the state entry
goes. Re-adopt it later with `terraform import`.

## Provider pin

`~> 4.52`. Cloudflare provider v5 renamed every resource used here
(`cloudflare_record` → `cloudflare_dns_record`, `cloudflare_zone_settings_override`
→ per-setting resources), so a v5 move is a module rewrite plus a
`terraform state mv` per record — schedule it as its own change.

## Tests

```bash
cd terraform/modules/cloudflare-zone
terraform init -backend=false
terraform test
```

`tests/validation.tftest.hcl` covers every variable validation, the four-way
record routing and the zone-settings switch. `terraform validate` evaluates no
caller values, so it runs none of them; the runs are plan-only against a
`mock_provider`, so they need no credentials and create no state. CI runs the
same command through `ci/validate/terraform.yml` with `test: true`.
