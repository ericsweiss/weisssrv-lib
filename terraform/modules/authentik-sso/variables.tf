variable "authorization_flow_slug" {
  description = <<-EOT
    Authorization flow every provider uses. The stock implicit-consent flow
    never prompts a signed-in user; switch to
    `default-provider-authorization-explicit-consent` to require a per-app
    consent screen (see README "Hardening notes").
  EOT
  type        = string
  default     = "default-provider-authorization-implicit-consent"
}

variable "invalidation_flow_slug" {
  description = "Invalidation (logout) flow every provider uses."
  type        = string
  default     = "default-provider-invalidation-flow"
}

variable "signing_key_name" {
  description = "Name of the certificate-keypair that signs OIDC tokens and SAML assertions."
  type        = string
  default     = "authentik Self-signed Certificate"
}

variable "oauth2_grant_types" {
  description = <<-EOT
    Default grant types for OAuth2 providers, overridable per provider.

    The default is the browser flow only. `password` (ROPC) bypasses the
    authentication flow's stages — including MFA — and `implicit`/`hybrid`
    return bearer tokens in the URL fragment, so none of them is enabled unless
    a real client needs it.
  EOT
  type        = list(string)
  default     = ["authorization_code", "refresh_token"]

  validation {
    condition = length(setsubtract(toset(var.oauth2_grant_types), toset([
      "authorization_code",
      "refresh_token",
      "implicit",
      "hybrid",
      "client_credentials",
      "password",
      "urn:ietf:params:oauth:grant-type:device_code",
    ]))) == 0
    error_message = "oauth2_grant_types may only contain grant types authentik supports."
  }
}

variable "oauth2_scope_mappings" {
  description = <<-EOT
    Scope property mappings assigned to every OAuth2 provider, in the order the
    API stores them (order is state, not style — a reordered list is a diff).

    An entry is a managed id, or `custom:<key>` naming a `custom_scope_mappings`
    entry this module authors.
  EOT
  type        = list(string)
  default = [
    "goauthentik.io/providers/oauth2/scope-openid",
    "goauthentik.io/providers/oauth2/scope-email",
    "goauthentik.io/providers/oauth2/scope-profile",
  ]
}

variable "custom_scope_mappings" {
  description = <<-EOT
    Scope property mappings this module AUTHORS, keyed by a stable identifier.
    Reference one from a provider's `scope_mappings` list (or from
    `oauth2_scope_mappings`) as `custom:<key>`; every other entry in those lists
    is a managed id read through a data source.

    This is the escape hatch for a claim authentik does not emit by default. Its
    built-in scope mappings are blueprint-managed objects the server restores on
    upgrade, so the claim has to live in a mapping of its own rather than an
    edit to a stock one.

    `expression` is authentik's Python expression body — it runs server-side on
    every token issue, so keep it side-effect free and never put a credential in
    it (mapping bodies are readable by any authentik admin).
  EOT
  type = map(object({
    name        = string
    scope_name  = string
    description = optional(string, "")
    expression  = string
  }))
  default = {}

  validation {
    condition = alltrue([
      for key, m in var.custom_scope_mappings : !startswith(key, "custom:")
    ])
    error_message = "custom_scope_mappings keys are bare identifiers; the \"custom:\" prefix belongs only in the scope_mappings reference."
  }
}

variable "saml_property_mappings" {
  description = "Managed IDs of the SAML property mappings assigned to every SAML provider, in the order the API stores them."
  type        = list(string)
  default = [
    "goauthentik.io/providers/saml/name",
    "goauthentik.io/providers/saml/email",
    "goauthentik.io/providers/saml/username",
    "goauthentik.io/providers/saml/uid",
    "goauthentik.io/providers/saml/groups",
    "goauthentik.io/providers/saml/upn",
    "goauthentik.io/providers/saml/ms-windowsaccountname",
  ]
}

variable "oauth2_providers" {
  description = <<-EOT
    OAuth2/OIDC providers keyed by a stable identifier (the key is the state
    address and the default client_id). Client secrets are passed separately in
    `oauth2_client_secrets` so this map stays non-sensitive and usable as a
    `for_each` source.
  EOT
  type = map(object({
    name        = string
    client_id   = optional(string)
    client_type = optional(string, "confidential")
    grant_types = optional(list(string))
    redirect_uris = list(object({
      url               = string
      matching_mode     = optional(string, "strict")
      redirect_uri_type = optional(string, "authorization")
    }))
    # Overrides oauth2_scope_mappings for this provider. Same two reference
    # kinds (managed id or "custom:<key>"), same ordering rule.
    scope_mappings             = optional(list(string))
    sub_mode                   = optional(string, "hashed_user_id")
    issuer_mode                = optional(string, "per_provider")
    include_claims_in_id_token = optional(bool, true)
    logout_method              = optional(string, "backchannel")
    access_code_validity       = optional(string, "minutes=1")
    access_token_validity      = optional(string, "minutes=5")
    refresh_token_validity     = optional(string, "days=30")
    refresh_token_threshold    = optional(string, "hours=1")
  }))
  default = {}

  validation {
    condition = alltrue(flatten([
      for key, p in var.oauth2_providers : [
        for r in p.redirect_uris : contains(["strict", "regex"], r.matching_mode)
      ]
    ]))
    error_message = "redirect_uris[*].matching_mode must be \"strict\" or \"regex\"."
  }

  validation {
    # authentik matches regex redirect URIs with re.fullmatch, where an
    # unescaped "." matches any character — so a plain URL as a pattern also
    # matches look-alike domains an attacker can register.
    condition = alltrue(flatten([
      for key, p in var.oauth2_providers : [
        for r in p.redirect_uris :
        length(regexall("(^|[^\\\\])\\.", r.url)) == 0
        if r.matching_mode == "regex"
      ]
    ]))
    # The doubled backslashes are HCL escapes: the message reads
    # `url = "https://app\\.example\\.com/callback"`, which is how the pattern
    # has to be written in a quoted HCL string (a single `\.` does not parse).
    error_message = "regex redirect URIs must escape every literal dot — write url = \"https://app\\\\.example\\\\.com/callback\" — because an unescaped dot matches look-alike domains."
  }

  validation {
    condition = alltrue([
      for key, p in var.oauth2_providers : contains(["confidential", "public"], p.client_type)
    ])
    error_message = "oauth2_providers[*].client_type must be \"confidential\" or \"public\"."
  }
}

variable "oauth2_client_secrets" {
  description = "Client secret per `oauth2_providers` key. A confidential provider without an entry gets a server-generated secret that only exists in state."
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "proxy_providers" {
  description = "Forward-auth (proxy) providers keyed by a stable identifier, served by the embedded outpost."
  type = map(object({
    name                         = string
    external_host                = string
    mode                         = optional(string, "forward_single")
    intercept_header_auth        = optional(bool, true)
    internal_host                = optional(string, "")
    internal_host_ssl_validation = optional(bool, true)
    skip_path_regex              = optional(string, "")
    cookie_domain                = optional(string, "")
    # Basic-auth injection: the outpost reads these attribute names off the user
    # (group attributes merge into user attributes) and sends the pair upstream.
    basic_auth_enabled            = optional(bool, false)
    basic_auth_username_attribute = optional(string, "")
    basic_auth_password_attribute = optional(string, "")
    access_token_validity         = optional(string, "hours=24")
    refresh_token_validity        = optional(string, "days=30")
  }))
  default = {}

  validation {
    condition = alltrue([
      for key, p in var.proxy_providers :
      !p.basic_auth_enabled || (p.basic_auth_username_attribute != "" && p.basic_auth_password_attribute != "")
    ])
    error_message = "proxy_providers with basic_auth_enabled must name both basic_auth_username_attribute and basic_auth_password_attribute."
  }
}

variable "saml_providers" {
  description = "SAML providers keyed by a stable identifier."
  type = map(object({
    name                            = string
    acs_url                         = string
    audience                        = optional(string, "")
    issuer_override                 = optional(string)
    sp_binding                      = optional(string, "post")
    sls_url                         = optional(string, "")
    sls_binding                     = optional(string, "redirect")
    logout_method                   = optional(string, "frontchannel_iframe")
    property_mappings               = optional(list(string))
    assertion_valid_not_before      = optional(string, "minutes=-5")
    assertion_valid_not_on_or_after = optional(string, "minutes=5")
    session_valid_not_on_or_after   = optional(string, "minutes=86400")
    digest_algorithm                = optional(string, "http://www.w3.org/2001/04/xmlenc#sha256")
    signature_algorithm             = optional(string, "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256")
    sign_assertion                  = optional(bool, true)
    sign_response                   = optional(bool, false)
    sign_logout_request             = optional(bool, false)
    sign_logout_response            = optional(bool, false)
    default_relay_state             = optional(string, "")
  }))
  default = {}
}

variable "groups" {
  description = "Groups keyed by a stable identifier (the key is the group name unless `name` overrides it). `users` holds usernames, which the module resolves to pks."
  type = map(object({
    name         = optional(string)
    is_superuser = optional(bool, false)
    users        = optional(list(string), [])
    attributes   = optional(map(string), {})
  }))
  default = {}
}

variable "group_secret_attributes" {
  description = "Sensitive attributes merged into a group's `attributes`, keyed by `groups` key — e.g. the credential pairs proxy providers inject."
  type        = map(map(string))
  default     = {}
  sensitive   = true
}

variable "applications" {
  description = <<-EOT
    Applications keyed by slug. `provider_type` + `provider_key` reference one of
    the provider maps; leave both null for an application with no provider.

    Each application must be named by at least one ENABLED `policy_bindings`
    entry or the plan fails — an unbound application is open to every
    authenticated user, and a disabled binding is not evaluated by the policy
    engine. `allow_unbound = true` declares that deliberate (a tile everyone may
    reach).
  EOT
  type = map(object({
    name               = string
    group              = optional(string, "")
    provider_type      = optional(string)
    provider_key       = optional(string)
    launch_url         = optional(string, "")
    icon               = optional(string, "")
    description        = optional(string, "")
    publisher          = optional(string, "")
    hide               = optional(bool, false)
    open_in_new_tab    = optional(bool, true)
    policy_engine_mode = optional(string, "any")
    allow_unbound      = optional(bool, false)
  }))
  default = {}

  validation {
    # `a.provider_type` bare, not `coalesce(a.provider_type, "oauth2")`: the
    # null case is already short-circuited, so the only value the coalesce
    # rewrote was the empty string — which it turned into "oauth2" and waved
    # through, leaving `""` to fail later against the provider map instead.
    condition = alltrue([
      for slug, a in var.applications :
      a.provider_type == null || contains(["oauth2", "proxy", "saml"], a.provider_type)
    ])
    error_message = "applications[*].provider_type must be \"oauth2\", \"proxy\", \"saml\", or unset."
  }

  validation {
    condition = alltrue([
      for slug, a in var.applications : (a.provider_type == null) == (a.provider_key == null)
    ])
    error_message = "applications[*] must set provider_type and provider_key together, or neither."
  }

  validation {
    condition = alltrue([
      for slug, a in var.applications : contains(["any", "all"], a.policy_engine_mode)
    ])
    error_message = "applications[*].policy_engine_mode must be \"any\" or \"all\"."
  }
}

variable "policy_bindings" {
  description = <<-EOT
    Group bindings that gate application access, keyed by a stable identifier.
    An application with no binding is open to every authenticated user, so give
    each application at least one; with policy_engine_mode "any", any one
    binding grants access. Only `enabled` bindings satisfy the unbound-application
    precondition — setting `enabled = false` on an application's last binding
    fails the plan rather than silently opening the app.
  EOT
  type = map(object({
    application = string
    group       = string
    order       = optional(number, 0)
    enabled     = optional(bool, true)
    negate      = optional(bool, false)
  }))
  default = {}
}

variable "embedded_outpost" {
  description = <<-EOT
    Provider assignment for authentik's embedded proxy outpost. null leaves the
    outpost alone.

    The outpost object is created by authentik itself, so managing it means
    importing it first (README "Adopting existing objects"). Going back to null
    later is a DESTROY of authentik's own outpost; `prevent_destroy` refuses it,
    so detach with `terraform state rm` instead.
  EOT
  type = object({
    name = optional(string, "authentik Embedded Outpost")
    # Ordered: the API preserves insertion order, so an out-of-order list is a
    # permanent diff.
    proxy_provider_keys = list(string)
  })
  default = null
}
