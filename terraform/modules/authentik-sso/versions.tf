terraform {
  # 1.5 floor: optional() object-attribute defaults and `import` blocks (the
  # adoption path documented in README.md).
  required_version = ">= 1.5, < 2.0"

  required_providers {
    authentik = {
      source = "goauthentik/authentik"
      # The provider is released in lockstep with the authentik server, and a
      # newer provider can carry schema for API fields an older server does not
      # serve. Callers pin the exact version matching their server; this floor
      # is the oldest release carrying the resource shapes used here.
      version = ">= 2026.5, < 2027.0"
    }
  }
}
