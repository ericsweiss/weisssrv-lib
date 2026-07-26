terraform {
  # 1.5 floor: optional() object-attribute defaults and `import` blocks (the
  # adoption path documented in README.md).
  required_version = ">= 1.5, < 2.0"

  required_providers {
    tailscale = {
      source = "tailscale/tailscale"
      # Pre-1.0 provider: a minor bump can carry breaking changes, so the minor
      # is pinned and the caller's lockfile pins the exact build.
      version = "~> 0.29"
    }
  }
}
