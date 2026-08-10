# authentik-sso

Authentik SSO objects as code: OAuth2/OIDC, forward-auth proxy and SAML
providers, applications, groups with memberships, the access-policy bindings
that gate them, and the embedded outpost's provider assignment.

The module is the **shape**; the object inventory (which apps, which groups,
which URLs) is site data the caller supplies.

## Consuming it

```hcl
module "sso" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/authentik-sso?ref=v0.6.0"

  oauth2_providers = {
    grafana = {
      name          = "Grafana"
      redirect_uris = [{ url = "https://grafana.example.com/login/generic_oauth" }]
    }
  }
  oauth2_client_secrets = {
    grafana = var.grafana_oidc_client_secret
  }

  proxy_providers = {
    adguard = {
      name                          = "AdGuard Home"
      external_host                 = "https://adguard.example.com"
      basic_auth_enabled            = true
      basic_auth_username_attribute = "adguard_user"
      basic_auth_password_attribute = "adguard_password"
    }
  }

  groups = {
    "grafana-users" = { users = ["alice"] }
    "dns-admins"    = { users = ["alice"] }
  }
  group_secret_attributes = {
    "dns-admins" = {
      adguard_user     = var.adguard_username
      adguard_password = var.adguard_password
    }
  }

  applications = {
    grafana = {
      name          = "Grafana"
      group         = "Software"
      provider_type = "oauth2"
      provider_key  = "grafana"
      launch_url    = "https://grafana.example.com"
    }
    adguard = {
      name          = "AdGuard Home"
      group         = "Software"
      provider_type = "proxy"
      provider_key  = "adguard"
      launch_url    = "https://adguard.example.com"
    }
  }

  policy_bindings = {
    grafana = { application = "grafana", group = "grafana-users" }
    adguard = { application = "adguard", group = "dns-admins" }
  }

  embedded_outpost = {
    proxy_provider_keys = ["adguard"]
  }
}
```

Provider and backend belong to the root module:

```hcl
provider "authentik" {
  url   = var.authentik_url # internal name, e.g. https://auth.internal.example.com
  token = var.authentik_token
}

terraform {
  backend "http" {} # its own state name
}
```

Pin the provider version to the running authentik server (`>= 2026.5, < 2027.0`
here): the provider ships in lockstep with the server, and a newer provider can
carry schema for API fields an older server does not serve.

## Inputs

| Input | Type | Default | Notes |
|---|---|---|---|
| `oauth2_providers` | map(object) | `{}` | Key = state address and default `client_id`. Requires `name` + `redirect_uris`. |
| `oauth2_client_secrets` | map(string), sensitive | `{}` | Keyed by provider key. A confidential provider with no entry gets a server-generated secret that only exists in state. |
| `oauth2_grant_types` | list(string) | `["authorization_code","refresh_token"]` | Per-provider override via `grant_types`. |
| `proxy_providers` | map(object) | `{}` | Forward-auth providers; `basic_auth_enabled` requires both attribute names. |
| `saml_providers` | map(object) | `{}` | Requires `name` + `acs_url`. |
| `groups` | map(object) | `{}` | Key = group name unless `name` overrides. `users` are usernames, resolved to pks. |
| `group_secret_attributes` | map(map(string)), sensitive | `{}` | Merged into a group's `attributes` — where basic-auth injection credentials live. |
| `applications` | map(object) | `{}` | Key = slug. `provider_type` (`oauth2`/`proxy`/`saml`) + `provider_key` wire the provider. |
| `policy_bindings` | map(object) | `{}` | `{application, group, order, enabled, negate}`. |
| `embedded_outpost` | object | `null` | `proxy_provider_keys` is ordered — the API preserves insertion order, so a reordered list is a permanent diff. |
| `authorization_flow_slug` | string | `default-provider-authorization-implicit-consent` | See hardening notes. |
| `invalidation_flow_slug` | string | `default-provider-invalidation-flow` | |
| `signing_key_name` | string | `authentik Self-signed Certificate` | Signs OIDC tokens and SAML assertions. |
| `oauth2_scope_mappings` / `saml_property_mappings` | list(string) | stock managed IDs | Order matters (it is the order the API stores). Per-provider override available. |

Users are never managed: only group membership is. Every username referenced in
`groups[*].users` must already exist.

## Outputs

`application_ids`, `application_uuids`, `group_ids`, `oauth2_provider_ids`,
`oauth2_client_ids`, `proxy_provider_ids`, `saml_provider_ids`,
`policy_binding_ids`.

## Security defaults

**Grant types.** `oauth2_grant_types` defaults to `authorization_code` +
`refresh_token` — the browser flow and nothing else. Do not add the rest without
a client that needs them:

- `password` (ROPC) accepts `grant_type=password&username=…&password=…` at the
  token endpoint and **bypasses the authentication flow's stages, including
  MFA**. The client secret is not a barrier — it is synced to every app that
  uses the provider.
- `implicit` / `hybrid` return bearer tokens in the URL fragment, which is what
  turns a loose redirect URI into token theft.
- `client_credentials` mints machine-to-machine tokens against the app's
  provider.

Importing an existing deployment usually pulls in all seven grants, because that
is what the Admin UI creates. Tighten them in a supervised apply with a login
smoke test per app.

**Redirect URIs.** `matching_mode` defaults to `strict`. authentik evaluates
`regex` mode with `re.fullmatch`, where `.` matches any character — so
`https://app.example.com/callback` as a regex also matches
`https://app-example.com/callback` and other registrable look-alike domains. The
module therefore **rejects a regex redirect URI containing an unescaped dot**:
write `https://app\.example\.com/callback`, and prefer `strict` unless a
wildcard is genuinely required.

**Consent flow.** The default authorization flow is authentik's
implicit-consent flow: an already-signed-in user is redirected without a consent
prompt. Set `authorization_flow_slug =
"default-provider-authorization-explicit-consent"` if you want a first-use
consent screen per application (each user sees it once per app).

**Basic-auth injection.** Credentials for upstreams that keep their own login
live in `group_secret_attributes`, i.e. as authentik group attributes that merge
into each member's user attributes; the proxy provider names the two attributes
and the outpost sends them upstream. Source them from the same secret store item
the app itself uses so the two can never disagree, and never as literals.

## Apply is supervised

An SSO misconfiguration locks every user out of every application at once. Apply
this module interactively, review the plan object by object, and keep a
break-glass path (a local admin account, or an app route that bypasses SSO)
available until the post-apply login checks pass.

## Adopting existing objects

Everything here can be brought under management with `import` blocks in the
**caller's** configuration — the module addresses are stable:

```hcl
import {
  to = module.sso.authentik_application.this["grafana"]
  id = "grafana"
}
import {
  to = module.sso.authentik_provider_oauth2.this["grafana"]
  id = "12" # provider pk
}
```

`authentik_group` names are **not unique** server-side and applications' slugs
**are**, so a disaster-recovery apply against a live server with an empty state
silently duplicates groups and hard-fails on applications. If you rely on
re-import for DR, keep the import blocks complete: enumerate the live pks/uuids
after every apply that creates objects (the `policy_binding_ids` and
`*_provider_ids` outputs give you the identifiers) rather than assuming an
import file written at adoption time still covers everything.
