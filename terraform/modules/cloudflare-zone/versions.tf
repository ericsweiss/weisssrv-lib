terraform {
  # 1.7 floor: the module's own guardrails are only exercised by the shipped
  # `tests/validation.tftest.hcl`, which runs under this same constraint, and
  # its `mock_provider` needs 1.7. The module's configuration itself needs 1.5
  # for optional() attribute defaults and the `import` blocks the README's
  # adoption path uses.
  required_version = ">= 1.7, < 2.0"

  required_providers {
    cloudflare = {
      source = "cloudflare/cloudflare"
      # v5 renamed every resource used here (cloudflare_record ->
      # cloudflare_dns_record, cloudflare_zone_settings_override -> per-setting
      # resources), so moving to v5 is a rewrite plus a `terraform state mv` per
      # record — never an incidental bump.
      version = "~> 4.52"
    }
  }
}
