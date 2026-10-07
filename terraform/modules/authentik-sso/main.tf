# Every `prevent_destroy` below protects an object whose destroy+create is a
# user-visible outage, so removing one is a two-step change — see README
# "Removing an object". Each lifecycle block states only the outage it prevents.

# Stock objects authentik creates and owns. Read by stable identifier, never
# managed.
data "authentik_flow" "authorization" {
  slug = var.authorization_flow_slug
}

data "authentik_flow" "invalidation" {
  slug = var.invalidation_flow_slug
}

data "authentik_certificate_key_pair" "signing" {
  name = var.signing_key_name
}

locals {
  # A scope reference is either a managed id (read through a data source) or
  # "custom:<key>" naming a custom_scope_mappings entry. One ordered list carries
  # both, because the API stores property_mappings in list order.
  oauth2_scope_refs = concat(
    var.oauth2_scope_mappings,
    flatten([
      for key, p in var.oauth2_providers : p.scope_mappings == null ? [] : p.scope_mappings
    ]),
  )

  oauth2_scope_mapping_ids = toset([
    for ref in local.oauth2_scope_refs : ref if !startswith(ref, "custom:")
  ])

  saml_property_mapping_ids = toset(concat(
    var.saml_property_mappings,
    flatten([
      for key, p in var.saml_providers : p.property_mappings == null ? [] : p.property_mappings
    ]),
  ))

  group_usernames = toset(flatten([for key, g in var.groups : g.users]))

  oauth2_scope_mappings = {
    for key, p in var.oauth2_providers :
    key => p.scope_mappings == null ? var.oauth2_scope_mappings : p.scope_mappings
  }

  saml_property_mappings = {
    for key, p in var.saml_providers :
    key => p.property_mappings == null ? var.saml_property_mappings : p.property_mappings
  }

  # Slugs a binding actually gates. A disabled binding is never evaluated, and a
  # negate-only binding admits every user OUTSIDE its group, so neither counts as
  # protection. An application missing here fails the precondition below.
  bound_application_slugs = toset([
    for key, b in var.policy_bindings : b.application if b.enabled && !b.negate
  ])

  group_attributes = {
    for key, g in var.groups :
    key => merge(g.attributes, lookup(var.group_secret_attributes, key, {}))
  }
}

data "authentik_property_mapping_provider_scope" "oauth2" {
  for_each = local.oauth2_scope_mapping_ids

  managed = each.value
}

# Caller-authored scope mappings. authentik restores its own blueprint-managed
# mappings on upgrade, so a claim the server does not emit by default needs a
# mapping of its own rather than an edit to a stock one.
resource "authentik_property_mapping_provider_scope" "custom" {
  for_each = var.custom_scope_mappings

  name        = each.value.name
  scope_name  = each.value.scope_name
  description = each.value.description
  expression  = each.value.expression

  lifecycle {
    # Destroying a mapping a provider still references strips the claim from
    # that provider's tokens — a login failure for any app that requires it.
    prevent_destroy = true
  }
}

locals {
  # Both reference spaces in one lookup table, so a provider's ordered list
  # resolves without caring which kind each entry is.
  scope_mapping_ids = merge(
    { for managed, d in data.authentik_property_mapping_provider_scope.oauth2 : managed => d.id },
    { for key, m in authentik_property_mapping_provider_scope.custom : "custom:${key}" => m.id },
  )
}

data "authentik_property_mapping_provider_saml" "saml" {
  for_each = local.saml_property_mapping_ids

  managed = each.value
}

# Managed user accounts, identity only: credentials and MFA live in authentik's
# enrollment/recovery flows. A destroy takes the account, its sessions and its
# consent grants, so a renamed key needs a `moved {}` block.
resource "authentik_user" "this" {
  for_each = var.users

  username  = each.key
  name      = each.value.name
  email     = each.value.email
  is_active = each.value.active
  path      = each.value.path

  lifecycle {
    prevent_destroy = true
    # authentik_group.this owns membership through its `users` list. Without
    # the ignore, this resource reconciles the server-side group list back to
    # empty on every apply.
    ignore_changes = [groups]
  }
}

# Pre-existing (UI-created) members referenced by group lists. Managed users are
# excluded: a data source cannot resolve a resource created in the same apply, so
# the membership expression below uses their resource pk.
data "authentik_user" "member" {
  for_each = setsubtract(local.group_usernames, toset(keys(var.users)))

  username = each.value
}

resource "authentik_provider_oauth2" "this" {
  for_each = var.oauth2_providers

  name          = each.value.name
  client_id     = coalesce(each.value.client_id, each.key)
  client_type   = each.value.client_type
  client_secret = lookup(var.oauth2_client_secrets, each.key, null)
  grant_types   = each.value.grant_types == null ? var.oauth2_grant_types : each.value.grant_types

  authorization_flow = data.authentik_flow.authorization.id
  invalidation_flow  = data.authentik_flow.invalidation.id
  signing_key        = data.authentik_certificate_key_pair.signing.id

  property_mappings = [
    for ref in local.oauth2_scope_mappings[each.key] :
    local.scope_mapping_ids[ref]
  ]

  allowed_redirect_uris = [
    for r in each.value.redirect_uris : {
      matching_mode     = r.matching_mode
      redirect_uri_type = r.redirect_uri_type
      url               = r.url
    }
  ]

  sub_mode                   = each.value.sub_mode
  issuer_mode                = each.value.issuer_mode
  include_claims_in_id_token = each.value.include_claims_in_id_token
  logout_method              = each.value.logout_method
  access_code_validity       = each.value.access_code_validity
  access_token_validity      = each.value.access_token_validity
  refresh_token_validity     = each.value.refresh_token_validity
  refresh_token_threshold    = each.value.refresh_token_threshold

  lifecycle {
    # A renamed map key plans as destroy+create, which mints a new provider and
    # breaks every session and token issued by the old one.
    prevent_destroy = true

    # `custom:` refs are resolved through a map, so a typo would otherwise
    # surface as a bare "Invalid index" naming neither the provider nor the key.
    precondition {
      condition = alltrue([
        for ref in local.oauth2_scope_mappings[each.key] :
        contains(keys(local.scope_mapping_ids), ref)
      ])
      error_message = "oauth2_providers[\"${each.key}\"].scope_mappings names a scope mapping that is neither a managed id nor a \"custom:<key>\" entry of custom_scope_mappings."
    }
  }
}

resource "authentik_provider_proxy" "this" {
  for_each = var.proxy_providers

  name          = each.value.name
  external_host = each.value.external_host

  mode                  = each.value.mode
  intercept_header_auth = each.value.intercept_header_auth

  authorization_flow = data.authentik_flow.authorization.id
  invalidation_flow  = data.authentik_flow.invalidation.id

  internal_host                = each.value.internal_host
  internal_host_ssl_validation = each.value.internal_host_ssl_validation
  skip_path_regex              = each.value.skip_path_regex
  cookie_domain                = each.value.cookie_domain

  basic_auth_enabled            = each.value.basic_auth_enabled
  basic_auth_username_attribute = each.value.basic_auth_username_attribute
  basic_auth_password_attribute = each.value.basic_auth_password_attribute

  access_token_validity  = each.value.access_token_validity
  refresh_token_validity = each.value.refresh_token_validity

  # property_mappings is deliberately unset: authentik auto-assigns the default
  # scope mappings to proxy providers, and the provider only manages this field
  # when it is configured — setting it leaves a permanent phantom diff.

  lifecycle {
    # Destroy+create here takes the application behind this provider off the
    # embedded outpost mid-apply, so every request to it 404s until the outpost
    # list is rewritten.
    prevent_destroy = true
  }
}

resource "authentik_provider_saml" "this" {
  for_each = var.saml_providers

  name            = each.value.name
  acs_url         = each.value.acs_url
  audience        = each.value.audience
  issuer_override = each.value.issuer_override
  sp_binding      = each.value.sp_binding
  sls_url         = each.value.sls_url
  sls_binding     = each.value.sls_binding
  logout_method   = each.value.logout_method

  authorization_flow = data.authentik_flow.authorization.id
  invalidation_flow  = data.authentik_flow.invalidation.id
  signing_kp         = data.authentik_certificate_key_pair.signing.id

  property_mappings = [
    for managed in local.saml_property_mappings[each.key] :
    data.authentik_property_mapping_provider_saml.saml[managed].id
  ]

  assertion_valid_not_before      = each.value.assertion_valid_not_before
  assertion_valid_not_on_or_after = each.value.assertion_valid_not_on_or_after
  session_valid_not_on_or_after   = each.value.session_valid_not_on_or_after

  digest_algorithm    = each.value.digest_algorithm
  signature_algorithm = each.value.signature_algorithm

  sign_assertion       = each.value.sign_assertion
  sign_response        = each.value.sign_response
  sign_logout_request  = each.value.sign_logout_request
  sign_logout_response = each.value.sign_logout_response

  default_relay_state = each.value.default_relay_state

  lifecycle {
    # The service provider trusts this provider's issuer and signing key, so a
    # destroy+create breaks the federation until the SP side is re-pointed.
    prevent_destroy = true
  }
}

resource "authentik_group" "this" {
  for_each = var.groups

  name         = coalesce(each.value.name, each.key)
  is_superuser = each.value.is_superuser
  users = [
    for username in each.value.users :
    contains(keys(var.users), username)
    # The resource exposes the pk as its string `id`; the data source as `pk`.
    ? tonumber(authentik_user.this[username].id)
    : data.authentik_user.member[username].pk
  ]

  # null, not `jsonencode({})`, for a group with no attributes: encoding an empty
  # object asserts a value, which diffs against an adopted group that carries any.
  attributes = (
    length(local.group_attributes[each.key]) == 0
    ? null
    : jsonencode(local.group_attributes[each.key])
  )

  lifecycle {
    # A destroy takes the memberships and every policy binding pointing at the
    # group with it, so a renamed key would revoke access for its members.
    prevent_destroy = true
  }
}

locals {
  provider_ids = merge(
    { for key, p in authentik_provider_oauth2.this : "oauth2/${key}" => p.id },
    { for key, p in authentik_provider_proxy.this : "proxy/${key}" => p.id },
    { for key, p in authentik_provider_saml.this : "saml/${key}" => p.id },
  )
}

resource "authentik_application" "this" {
  for_each = var.applications

  name  = each.value.name
  slug  = each.key
  group = each.value.group
  protocol_provider = (
    each.value.provider_type == null
    ? null
    : local.provider_ids["${each.value.provider_type}/${each.value.provider_key}"]
  )

  meta_launch_url  = each.value.launch_url
  meta_icon        = each.value.icon
  meta_description = each.value.description
  meta_publisher   = each.value.publisher
  meta_hide        = each.value.hide

  open_in_new_tab    = each.value.open_in_new_tab
  policy_engine_mode = each.value.policy_engine_mode

  lifecycle {
    # The map key is the slug, and the slug is the OIDC issuer path
    # (/application/o/<slug>/), so a renamed key plans as destroy+create and
    # every client configured against the old issuer fails.
    prevent_destroy = true

    # provider_type/provider_key resolve through a map, so a typo would
    # otherwise surface as a bare "Invalid index" naming neither.
    precondition {
      # Conditional, not `||`: both halves must stay unevaluated when the
      # application names no provider.
      condition = (
        each.value.provider_type == null
        ? true
        : contains(keys(local.provider_ids), "${each.value.provider_type}/${each.value.provider_key}")
      )
      # error_message is evaluated eagerly, so each half is null-guarded; a
      # conditional rather than coalesce, which would also rewrite the empty
      # string.
      error_message = "applications[\"${each.key}\"] references provider \"${each.value.provider_type == null ? "<unset>" : each.value.provider_type}/${each.value.provider_key == null ? "<unset>" : each.value.provider_key}\", which is not a key of the matching provider map (oauth2_providers, proxy_providers or saml_providers)."
    }

    # An unbound application is reachable by every authenticated user and
    # otherwise plans cleanly. A precondition, not a `check` block: check
    # assertions are only warnings, and this has to fail the plan.
    precondition {
      condition = (
        each.value.allow_unbound ||
        contains(local.bound_application_slugs, each.key)
      )
      error_message = "applications[\"${each.key}\"] has no enabled, non-negated entry in policy_bindings; an unbound application is open to every authenticated user. A binding with enabled = false does not count (the policy engine never evaluates it), and neither does one with negate = true (it admits everyone outside the group). Add an allow binding, or set allow_unbound = true to declare that deliberate."
    }
  }
}

resource "authentik_policy_binding" "this" {
  for_each = var.policy_bindings

  target  = authentik_application.this[each.value.application].uuid
  group   = authentik_group.this[each.value.group].id
  order   = each.value.order
  enabled = each.value.enabled
  negate  = each.value.negate

  lifecycle {
    # Both sides resolve through a map, so a typo would otherwise surface as a
    # bare "Invalid index" naming neither the binding nor the key.
    precondition {
      condition     = contains(keys(var.applications), each.value.application)
      error_message = "policy_bindings[\"${each.key}\"].application = \"${each.value.application}\" is not an `applications` key (the key is the application slug)."
    }

    precondition {
      condition     = contains(keys(var.groups), each.value.group)
      error_message = "policy_bindings[\"${each.key}\"].group = \"${each.value.group}\" is not a `groups` key (the key, not the group's `name` override)."
    }
  }
}

resource "authentik_outpost" "embedded" {
  count = var.embedded_outpost == null ? 0 : 1

  name = var.embedded_outpost.name
  type = "proxy"

  protocol_providers = [
    for key in var.embedded_outpost.proxy_provider_keys : authentik_provider_proxy.this[key].id
  ]

  # config and service_connection are deliberately unset: both are
  # Optional+Computed, so leaving them alone lets the authentik-managed values
  # round-trip instead of being diffed and rewritten.

  lifecycle {
    # authentik's own object, adopted by import: a destroy removes forward auth
    # for every proxy provider at once. Setting embedded_outpost back to null is
    # that same destroy — detach with `terraform state rm` instead.
    prevent_destroy = true

    # proxy_provider_keys resolves through a map, so a typo would otherwise
    # surface as a bare "Invalid index" naming neither the outpost nor the key.
    precondition {
      condition = alltrue([
        for key in var.embedded_outpost.proxy_provider_keys :
        contains(keys(var.proxy_providers), key)
      ])
      error_message = "embedded_outpost.proxy_provider_keys names a key that is not in proxy_providers; the outpost serves proxy providers this module manages."
    }

    # The reverse direction is the one that fails in production: the outpost
    # serves only the keys named here, so a proxy provider left off the list
    # plans clean and 404s at the edge.
    precondition {
      condition = length(setsubtract(
        toset([for key, p in var.proxy_providers : key if !p.detached]),
        toset(var.embedded_outpost.proxy_provider_keys),
      )) == 0
      error_message = "a proxy_providers entry is missing from embedded_outpost.proxy_provider_keys; the outpost serves only the keys named there, so an omitted forward-auth provider 404s at the edge. Add it to the list, or set detached = true on it."
    }
  }
}
