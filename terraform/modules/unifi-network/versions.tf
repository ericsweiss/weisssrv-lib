terraform {
  # 1.7 floor: the module's own guardrails are only exercised by the shipped
  # `tests/validation.tftest.hcl`, which runs under this same constraint, and
  # its `mock_provider` needs 1.7. The module's configuration itself needs 1.5
  # for optional() attribute defaults and the `import` blocks the README's
  # adoption path uses.
  required_version = ">= 1.7, < 2.0"

  required_providers {
    unifi = {
      source = "ubiquiti-community/unifi"
      # Pre-1.0 provider, and a rewrite of the abandoned paultyng one: the 0.52
      # -> 0.55 minors changed resource shapes (firewall-policy `index` made
      # read-only, `unifi_network.purpose` added, endpoint match lists made
      # Computed), so the minor is pinned and the caller's lockfile pins the
      # exact build. `~> 0.55` would float every 0.x minor — `~>` only pins
      # everything left of the last component.
      version = "~> 0.55.0"
    }
  }
}
