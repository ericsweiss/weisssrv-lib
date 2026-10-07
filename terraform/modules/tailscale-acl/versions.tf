terraform {
  # Floor is the test harness's (mock_provider/override_data), not the
  # configuration's — see ../README.md.
  required_version = ">= 1.7, < 2.0"

  required_providers {
    tailscale = {
      source = "tailscale/tailscale"
      # Pre-1.0, pinned to the minor (../README.md).
      version = "~> 0.29.0"
    }
  }
}
