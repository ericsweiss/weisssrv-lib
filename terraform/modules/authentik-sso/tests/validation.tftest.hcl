# `terraform validate` evaluates no caller values, so nothing else exercises the
# variable validations, the preconditions or the reference resolution here.
# Every run is `command = plan`, which creates no state to tear down.
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

# Under policy_engine_mode "any" a negated binding admits every user OUTSIDE the
# group, so on its own it is a deny list, not a gate.
run "a_negate_only_binding_does_not_count_as_bound" {
  command = plan

  variables {
    policy_bindings = {
      grafana = { application = "grafana", group = "app-grafana", negate = true }
    }
  }

  expect_failures = [authentik_application.this]
}

run "an_allow_binding_alongside_a_negate_binding_plans" {
  command = plan

  variables {
    groups = {
      "app-grafana" = { users = [] }
      "suspended"   = { users = [] }
    }
    policy_bindings = {
      grafana = { application = "grafana", group = "app-grafana" }
      denied  = { application = "grafana", group = "suspended", negate = true }
    }
  }

  assert {
    condition     = authentik_application.this["grafana"].slug == "grafana"
    error_message = "A negate binding paired with an allow binding must plan."
  }
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

# An application may be a plain launch tile with no provider. This run holds the
# precondition to its conditional form: `||` evaluates both operands, which would
# interpolate the null and fail the plan.
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
# precondition catches it — and its message has to print rather than fail
# evaluating, which a coalesce() around either half would do.
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
      "app-grafana" = { users = ["amy", "bob"] }
    }
  }

  assert {
    condition     = contains(keys(data.authentik_user.member), "bob")
    error_message = "pre-existing username missing from the data lookup set"
  }
}

# The outpost serves only the keys it is given, so a provider left off it plans
# clean and 404s at the edge.
run "a_proxy_provider_missing_from_the_outpost_fails_the_plan" {
  command = plan

  variables {
    proxy_providers = {
      dashboard = { name = "Dashboard", external_host = "https://dashboard.example.com" }
      wiki      = { name = "Wiki", external_host = "https://wiki.example.com" }
    }
    embedded_outpost = {
      proxy_provider_keys = ["dashboard"]
    }
  }

  expect_failures = [authentik_outpost.embedded]
}

run "a_detached_proxy_provider_need_not_be_on_the_outpost" {
  command = plan

  variables {
    proxy_providers = {
      dashboard = { name = "Dashboard", external_host = "https://dashboard.example.com" }
      wiki      = { name = "Wiki", external_host = "https://wiki.example.com", detached = true }
    }
    embedded_outpost = {
      proxy_provider_keys = ["dashboard"]
    }
  }

  assert {
    condition     = length(authentik_outpost.embedded[0].protocol_providers) == 1
    error_message = "The outpost must carry exactly the keys it names."
  }
}

run "proxy_providers_with_no_outpost_at_all_fail_the_plan" {
  command = plan

  variables {
    proxy_providers = {
      dashboard = { name = "Dashboard", external_host = "https://dashboard.example.com" }
    }
  }

  expect_failures = [var.embedded_outpost]
}

run "a_saml_provider_plans_with_the_default_property_mappings" {
  command = plan

  variables {
    applications = {
      wiki = {
        name          = "Wiki"
        provider_type = "saml"
        provider_key  = "wiki"
      }
    }
    saml_providers = {
      wiki = {
        name    = "Wiki"
        acs_url = "https://wiki.example.com/saml/acs"
      }
    }
    groups = {
      "app-wiki" = { users = [] }
    }
    policy_bindings = {
      wiki = { application = "wiki", group = "app-wiki" }
    }
  }

  assert {
    condition     = length(authentik_provider_saml.this["wiki"].property_mappings) == 7
    error_message = "The seven default SAML property mappings must resolve through the managed-id data sources."
  }

  assert {
    condition     = authentik_provider_saml.this["wiki"].acs_url == "https://wiki.example.com/saml/acs"
    error_message = "The SAML provider must plan with the caller's ACS URL."
  }
}

# "custom:<key>" is an OAuth2-only reference form; the SAML list is read straight
# through a data source, where a bad id is an opaque read failure.
run "rejects_a_custom_prefixed_saml_property_mapping" {
  command = plan

  variables {
    saml_providers = {
      wiki = {
        name              = "Wiki"
        acs_url           = "https://wiki.example.com/saml/acs"
        property_mappings = ["custom:groups"]
      }
    }
  }

  expect_failures = [var.saml_providers]
}

run "rejects_a_custom_prefixed_default_saml_property_mapping" {
  command = plan

  variables {
    saml_property_mappings = ["custom:groups"]
  }

  expect_failures = [var.saml_property_mappings]
}

# A dot whose backslash is itself escaped is still a wildcard in the stored
# pattern, so the check counts the backslash run rather than one character.
run "rejects_a_regex_redirect_uri_whose_dot_follows_an_escaped_backslash" {
  command = plan

  variables {
    oauth2_providers = {
      grafana = {
        name = "Grafana"
        redirect_uris = [{
          url           = "https://grafana\\\\.example\\.com/login"
          matching_mode = "regex"
        }]
      }
    }
  }

  expect_failures = [var.oauth2_providers]
}

# Both sensitive maps are consumed with lookup(), so an orphan key would
# otherwise be dropped in silence.
run "rejects_an_orphan_oauth2_client_secret_key" {
  command = plan

  variables {
    oauth2_client_secrets = {
      grafanna = "test-secret-unit-only"
    }
  }

  expect_failures = [var.oauth2_client_secrets]
}

run "rejects_an_orphan_group_secret_attributes_key" {
  command = plan

  variables {
    group_secret_attributes = {
      "app-grafanna" = { dashboard_password = "test-password-unit-only" }
    }
  }

  expect_failures = [var.group_secret_attributes]
}
