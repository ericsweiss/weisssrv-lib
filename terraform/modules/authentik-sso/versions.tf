terraform {
  # 1.11 floor: the module's own guardrails are only exercised by the shipped
  # `tests/validation.tftest.hcl`, which runs under this same constraint —
  # `mock_provider`/`override_resource` need 1.7 and `override_during = plan`
  # (the only way an override lands in a plan-only run) needs 1.11. The module's
  # configuration itself needs 1.5 for optional() attribute defaults and the
  # `import` blocks the README's adoption path uses.
  required_version = ">= 1.11, < 2.0"

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
