terraform {
  # 1.7 floor: the module's own guardrails are only exercised by the shipped
  # `tests/validation.tftest.hcl`, which runs under this same constraint, and
  # its `mock_provider`/`override_data` blocks need 1.7. The module's
  # configuration itself needs 1.5 for optional() attribute defaults and the
  # `import` blocks the README's adoption path uses.
  required_version = ">= 1.7, < 2.0"

  required_providers {
    tailscale = {
      source = "tailscale/tailscale"
      # Pre-1.0 provider: a minor bump can carry breaking changes, so the minor
      # is pinned and the caller's lockfile pins the exact build. Three
      # components are required for that — `~> 0.29` would float every 0.x
      # minor, since `~>` only pins everything left of the last component.
      version = "~> 0.29.0"
    }
  }
}
