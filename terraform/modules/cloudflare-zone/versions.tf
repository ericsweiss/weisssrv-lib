terraform {
  # The test harness's floor (mock_provider): ../README.md.
  required_version = ">= 1.7, < 2.0"

  required_providers {
    cloudflare = {
      source = "cloudflare/cloudflare"
      # v5 renames every resource used here, so moving to v5 is a rewrite plus
      # a `terraform state mv` per record, never an incidental bump.
      version = "~> 4.52"
    }
  }
}
