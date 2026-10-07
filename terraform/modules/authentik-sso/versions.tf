terraform {
  # Floor is the test harness's (override_during = plan), not the
  # configuration's, which needs 1.9 — see ../README.md.
  required_version = ">= 1.11, < 2.0"

  required_providers {
    authentik = {
      source = "goauthentik/authentik"
      # The provider ships in lockstep with the authentik server, so callers pin
      # the exact version matching theirs. This floor is the oldest release
      # carrying the resource shapes used here.
      version = ">= 2026.5, < 2027.0"
    }
  }
}
