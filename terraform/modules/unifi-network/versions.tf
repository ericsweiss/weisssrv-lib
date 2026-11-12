terraform {
  # Write-only arguments (`passphrase_wo`): ../README.md.
  required_version = ">= 1.11, < 2.0"

  required_providers {
    unifi = {
      source = "ubiquiti-community/unifi"
      # Pre-1.0, pinned to the minor (../README.md).
      version = "~> 0.65.0"
    }
  }
}
