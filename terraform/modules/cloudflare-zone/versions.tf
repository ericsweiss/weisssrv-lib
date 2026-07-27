terraform {
  # 1.5 floor: optional() object-attribute defaults and `import` blocks (the
  # adoption path documented in README.md).
  required_version = ">= 1.5, < 2.0"

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
