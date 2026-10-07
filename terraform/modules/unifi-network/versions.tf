terraform {
  # Floor is the test harness's (mock_provider), not the configuration's — see
  # ../README.md.
  required_version = ">= 1.7, < 2.0"

  required_providers {
    unifi = {
      source = "ubiquiti-community/unifi"
      # Pre-1.0, pinned to the minor (../README.md).
      version = "~> 0.55.0"
    }
  }
}
