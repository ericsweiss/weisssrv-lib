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
  oauth2_scope_mapping_ids = toset(concat(
    var.oauth2_scope_mappings,
    flatten([
      for key, p in var.oauth2_providers : p.scope_mappings == null ? [] : p.scope_mappings
    ]),
  ))

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
}

data "authentik_property_mapping_provider_scope" "oauth2" {
  for_each = local.oauth2_scope_mapping_ids

  managed = each.value
}

data "authentik_property_mapping_provider_saml" "saml" {
  for_each = local.saml_property_mapping_ids

  managed = each.value
}

# Users are never managed here: their credentials and MFA live outside
# Terraform. Only group membership is.
data "authentik_user" "member" {
  for_each = local.group_usernames

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
    for managed in local.oauth2_scope_mappings[each.key] :
    data.authentik_property_mapping_provider_scope.oauth2[managed].id
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
}

resource "authentik_group" "this" {
  for_each = var.groups

  name         = coalesce(each.value.name, each.key)
  is_superuser = each.value.is_superuser
  users        = [for username in each.value.users : data.authentik_user.member[username].pk]
  attributes = jsonencode(merge(
    each.value.attributes,
    lookup(var.group_secret_attributes, each.key, {}),
  ))
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
}

resource "authentik_policy_binding" "this" {
  for_each = var.policy_bindings

  target  = authentik_application.this[each.value.application].uuid
  group   = authentik_group.this[each.value.group].id
  order   = each.value.order
  enabled = each.value.enabled
  negate  = each.value.negate
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
}
