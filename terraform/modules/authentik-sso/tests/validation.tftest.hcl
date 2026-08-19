# `terraform validate` evaluates no caller values, so nothing else exercises the
# variable validations, the preconditions or the reference resolution that
# builds the 40-odd SSO objects. Every run is `command = plan`: a plan creates
# no state, so the file needs no teardown — which this module's `prevent_destroy`
# resources would refuse anyway.
mock_provider "authentik" {}

variables {
  applications = {
    grafana = {
      name          = "Grafana"
      provider_type = "oauth2"
      provider_key  = "grafana"
    }
  }
  oauth2_providers = {
    grafana = {
      name          = "Grafana"
      redirect_uris = [{ url = "https://grafana.example.com/login/generic_oauth" }]
    }
  }
  groups = {
    "app-grafana" = { users = [] }
  }
  policy_bindings = {
    grafana = { application = "grafana", group = "app-grafana" }
  }
}

run "a_bound_application_plans" {
  command = plan

  assert {
    condition     = authentik_application.this["grafana"].slug == "grafana"
    error_message = "The applications map key is the slug, and the slug is the OIDC issuer path."
  }

  assert {
    condition     = authentik_provider_oauth2.this["grafana"].client_id == "grafana"
    error_message = "client_id defaults to the oauth2_providers map key."
  }

  assert {
    condition     = length(authentik_provider_oauth2.this["grafana"].property_mappings) == 3
    error_message = "The three default scope mappings must resolve through the managed-id data sources."
  }

  assert {
    condition = authentik_provider_oauth2.this["grafana"].grant_types == tolist([
      "authorization_code", "refresh_token"
    ])
    error_message = "The default grant types are the browser flow only — no password/implicit/hybrid."
  }
}

# The one guardrail here that fails OPEN: an unbound application is reachable by
# every authenticated user, and a missing binding otherwise plans cleanly.
run "an_unbound_application_fails_the_plan" {
  command = plan

  variables {
    policy_bindings = {}
  }

  expect_failures = [authentik_application.this]
}

run "a_disabled_binding_does_not_count_as_bound" {
  command = plan

  variables {
    policy_bindings = {
      grafana = { application = "grafana", group = "app-grafana", enabled = false }
    }
  }

  expect_failures = [authentik_application.this]
}

run "allow_unbound_declares_an_open_tile_deliberate" {
  command = plan

  variables {
    applications = {
      grafana = {
        name          = "Grafana"
        provider_type = "oauth2"
        provider_key  = "grafana"
        allow_unbound = true
      }
    }
    policy_bindings = {}
  }

  assert {
    condition     = authentik_application.this["grafana"].slug == "grafana"
    error_message = "allow_unbound = true must plan without a policy binding."
  }
}

# provider_type/provider_key are optional: an application can be a plain launch
# tile with no protocol provider behind it. Terraform evaluates BOTH operands of
# `||`, so writing the precondition below as `provider_type == null || contains(…)`
# interpolates the null into the right-hand template and fails the plan with
# "Invalid template interpolation value" instead of planning. This run is what
# holds that precondition to its conditional-expression form.
run "an_application_with_no_provider_plans" {
  command = plan

  variables {
    applications = {
      grafana = {
        name       = "Grafana"
        launch_url = "https://grafana.example.com/"
      }
    }
  }

  assert {
    condition     = authentik_application.this["grafana"].protocol_provider == null
    error_message = "An application naming no provider must plan with protocol_provider unset."
  }

  assert {
    condition     = authentik_application.this["grafana"].meta_launch_url == "https://grafana.example.com/"
    error_message = "A provider-less application is still a launch tile — the rest of the resource must plan."
  }
}

# A typo in any cross-map reference would otherwise surface as a bare
# "Invalid index" naming neither the map nor the key.
run "an_unknown_provider_key_fails_the_plan" {
  command = plan

  variables {
    applications = {
      grafana = {
        name          = "Grafana"
        provider_type = "oauth2"
        provider_key  = "graphana"
      }
    }
  }

  expect_failures = [authentik_application.this]
}

run "a_binding_naming_an_unknown_application_fails_the_plan" {
  command = plan

  variables {
    policy_bindings = {
      grafana = { application = "grafana", group = "app-grafana" }
      stray   = { application = "grafna", group = "app-grafana" }
    }
  }

  expect_failures = [authentik_policy_binding.this]
}

run "a_binding_naming_an_unknown_group_fails_the_plan" {
  command = plan

  variables {
    policy_bindings = {
      grafana = { application = "grafana", group = "app-grafanaa" }
    }
  }

  expect_failures = [authentik_policy_binding.this]
}

run "an_outpost_naming_an_unknown_proxy_provider_fails_the_plan" {
  command = plan

  variables {
    embedded_outpost = {
      proxy_provider_keys = ["nowhere"]
    }
  }

  expect_failures = [authentik_outpost.embedded]
}

run "an_unknown_scope_mapping_reference_fails_the_plan" {
  command = plan

  variables {
    oauth2_providers = {
      grafana = {
        name           = "Grafana"
        redirect_uris  = [{ url = "https://grafana.example.com/login/generic_oauth" }]
        scope_mappings = ["custom:groups"]
      }
    }
  }

  expect_failures = [authentik_provider_oauth2.this]
}

# authentik matches regex redirect URIs with re.fullmatch, where an unescaped
# "." also matches a registrable look-alike domain.
run "rejects_a_regex_redirect_uri_with_an_unescaped_dot" {
  command = plan

  variables {
    oauth2_providers = {
      grafana = {
        name = "Grafana"
        redirect_uris = [{
          url           = "https://grafana.example.com/.*"
          matching_mode = "regex"
        }]
      }
    }
  }

  expect_failures = [var.oauth2_providers]
}

run "accepts_a_regex_redirect_uri_with_escaped_dots" {
  command = plan

  variables {
    oauth2_providers = {
      grafana = {
        name = "Grafana"
        redirect_uris = [{
          url           = "https://grafana\\.example\\.com/login/[a-z_]+"
          matching_mode = "regex"
        }]
      }
    }
  }

  assert {
    condition     = length(authentik_provider_oauth2.this["grafana"].allowed_redirect_uris) == 1
    error_message = "An escaped-dot regex redirect URI is the documented form and must plan."
  }
}

run "rejects_an_unknown_matching_mode" {
  command = plan

  variables {
    oauth2_providers = {
      grafana = {
        name          = "Grafana"
        redirect_uris = [{ url = "https://grafana.example.com/", matching_mode = "prefix" }]
      }
    }
  }

  expect_failures = [var.oauth2_providers]
}

run "rejects_an_unknown_client_type" {
  command = plan

  variables {
    oauth2_providers = {
      grafana = {
        name          = "Grafana"
        client_type   = "native"
        redirect_uris = [{ url = "https://grafana.example.com/" }]
      }
    }
  }

  expect_failures = [var.oauth2_providers]
}

run "rejects_an_unsupported_grant_type" {
  command = plan

  variables {
    oauth2_grant_types = ["authorization_code", "magic_link"]
  }

  expect_failures = [var.oauth2_grant_types]
}

run "rejects_a_custom_scope_mapping_key_carrying_the_prefix" {
  command = plan

  variables {
    custom_scope_mappings = {
      "custom:groups" = {
        name       = "groups"
        scope_name = "groups"
        expression = "return {}"
      }
    }
  }

  expect_failures = [var.custom_scope_mappings]
}

run "rejects_an_application_with_only_half_a_provider_reference" {
  command = plan

  variables {
    applications = {
      grafana = {
        name          = "Grafana"
        provider_type = "oauth2"
      }
    }
  }

  expect_failures = [var.applications]
}

run "rejects_an_unknown_provider_type" {
  command = plan

  variables {
    applications = {
      grafana = {
        name          = "Grafana"
        provider_type = "ldap"
        provider_key  = "grafana"
      }
    }
  }

  expect_failures = [var.applications]
}

# The empty string is not "unset": it names no provider map, so it has to fail
# the enum rather than be rewritten into a default.
run "rejects_an_empty_provider_type" {
  command = plan

  variables {
    applications = {
      grafana = {
        name          = "Grafana"
        provider_type = ""
        provider_key  = ""
      }
    }
  }

  expect_failures = [var.applications]
}

# An empty provider_key passes the "set both or neither" validation, so the
# precondition is what catches it — and its message has to PRINT rather than
# fail evaluating (a `coalesce(…, "")` around either half raises "no non-null,
# non-empty-string arguments" here instead of naming the bad reference).
run "an_empty_provider_key_reports_the_missing_reference" {
  command = plan

  variables {
    applications = {
      grafana = {
        name          = "Grafana"
        provider_type = "oauth2"
        provider_key  = ""
      }
    }
  }

  expect_failures = [authentik_application.this]
}

run "rejects_an_unknown_policy_engine_mode" {
  command = plan

  variables {
    applications = {
      grafana = {
        name               = "Grafana"
        provider_type      = "oauth2"
        provider_key       = "grafana"
        policy_engine_mode = "every"
      }
    }
  }

  expect_failures = [var.applications]
}

run "rejects_basic_auth_without_both_attribute_names" {
  command = plan

  variables {
    proxy_providers = {
      dashboard = {
        name                          = "Dashboard"
        external_host                 = "https://dashboard.example.com"
        basic_auth_enabled            = true
        basic_auth_username_attribute = "dashboard_user"
      }
    }
  }

  expect_failures = [var.proxy_providers]
}

# A custom mapping is referenced as "custom:<key>" and interleaves with managed
# ids in one ordered list, because the API stores property_mappings in order.
run "custom_and_managed_scope_references_resolve_in_order" {
  command = plan

  variables {
    custom_scope_mappings = {
      groups = {
        name       = "app groups"
        scope_name = "groups"
        expression = "return {\"groups\": [g.name for g in request.user.ak_groups.all()]}"
      }
    }
    oauth2_providers = {
      grafana = {
        name          = "Grafana"
        redirect_uris = [{ url = "https://grafana.example.com/login/generic_oauth" }]
        scope_mappings = [
          "goauthentik.io/providers/oauth2/scope-openid",
          "custom:groups",
        ]
      }
    }
  }

  # The mapping's id is computed, so pin it during the plan to assert on it.
  override_resource {
    target          = authentik_property_mapping_provider_scope.custom["groups"]
    override_during = plan
    values = {
      id = "custom-groups-mapping"
    }
  }

  assert {
    condition     = length(authentik_provider_oauth2.this["grafana"].property_mappings) == 2
    error_message = "Both reference kinds must resolve, in the order the caller listed them."
  }

  assert {
    condition     = authentik_provider_oauth2.this["grafana"].property_mappings[1] == "custom-groups-mapping"
    error_message = "A \"custom:<key>\" reference must resolve to the mapping this module authors, in list position."
  }
}

# Secret attributes merge into the group's plain attributes; a group with
# neither must send null rather than an empty JSON object.
run "group_attributes_merge_and_stay_null_when_empty" {
  command = plan

  variables {
    groups = {
      "app-grafana" = {
        users      = []
        attributes = { theme = "dark" }
      }
      "app-empty" = { users = [] }
    }
    group_secret_attributes = {
      "app-grafana" = { dashboard_password = "test-password-unit-only" }
    }
    policy_bindings = {
      grafana = { application = "grafana", group = "app-grafana" }
    }
  }

  assert {
    condition = authentik_group.this["app-grafana"].attributes == jsonencode({
      dashboard_password = "test-password-unit-only"
      theme              = "dark"
    })
    error_message = "group_secret_attributes must merge into the group's attributes."
  }

  assert {
    condition     = authentik_group.this["app-empty"].attributes == null
    error_message = "A group with no attributes must send null, not an empty JSON object."
  }
}

# Managed users: the resource plans, and a group list mixing a managed user
# with a pre-existing one resolves the managed name to the resource (the data
# lookup set must EXCLUDE it, or the plan fails on an unresolvable data read).
run "a_managed_user_plans_and_resolves_group_membership" {
  command = plan

  variables {
    users = {
      "amy" = { name = "Amy", email = "amy@example.com" }
    }
    groups = {
      "app-grafana" = { users = ["amy"] }
    }
  }

  assert {
    condition     = authentik_user.this["amy"].username == "amy"
    error_message = "managed user resource did not plan with its username key"
  }
  assert {
    condition     = !contains(keys(data.authentik_user.member), "amy")
    error_message = "managed username leaked into the pre-existing-user data lookup set"
  }
}

run "an_unmanaged_group_member_still_resolves_via_data" {
  command = plan

  variables {
    users = {
      "amy" = { name = "Amy", email = "amy@example.com" }
    }
    groups = {
      "app-grafana" = { users = ["amy", "eric"] }
    }
  }

  assert {
    condition     = contains(keys(data.authentik_user.member), "eric")
    error_message = "pre-existing username missing from the data lookup set"
  }
}
