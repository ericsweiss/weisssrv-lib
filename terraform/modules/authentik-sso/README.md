# authentik-sso

Authentik SSO objects as code: OAuth2/OIDC, forward-auth proxy and SAML
providers, applications, groups with memberships, the access-policy bindings
that gate them, and the embedded outpost's provider assignment.

The module is the **shape**; the object inventory (which apps, which groups,
which URLs) is site data the caller supplies.

## Consuming it

The tag below is an example: use the tag your repo pins (docs/VERSIONING.md).

```hcl
module "sso" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/authentik-sso?ref=v0.9.0"

  oauth2_providers = {
    grafana = {
      name          = "Grafana"
      redirect_uris = [{ url = "https://grafana.example.com/login/generic_oauth" }]
    }
    recipes = {
      name          = "Recipes"
      redirect_uris = [{ url = "https://recipes.example.com/login" }]
      # Stock openid + profile, with the module-authored email scope in place of
      # the built-in one. Order is what the API stores.
      scope_mappings = [
        "goauthentik.io/providers/oauth2/scope-openid",
        "custom:email_verified",
        "goauthentik.io/providers/oauth2/scope-profile",
      ]
    }
  }
  oauth2_client_secrets = {
    grafana = var.grafana_oidc_client_secret
    recipes = var.recipes_oidc_client_secret
  }

  custom_scope_mappings = {
    email_verified = {
      name       = "OIDC email (asserted verified)"
      scope_name = "email"
      expression = <<-EOT
        return {
            "email": request.user.email,
            "email_verified": True,
        }
      EOT
    }
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
    "recipes-users" = { users = ["alice"] }
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
    recipes = {
      name          = "Recipes"
      group         = "Home"
      provider_type = "oauth2"
      provider_key  = "recipes"
      launch_url    = "https://recipes.example.com"
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
    recipes = { application = "recipes", group = "recipes-users" }
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
| `applications` | map(object) | `{}` | Key = slug. `provider_type` (`oauth2`/`proxy`/`saml`) + `provider_key` wire the provider. Needs a `policy_bindings` entry unless `allow_unbound = true`. |
| `policy_bindings` | map(object) | `{}` | `{application, group, order, enabled, negate}`. |
| `embedded_outpost` | object | `null` | `proxy_provider_keys` is ordered — the API preserves insertion order, so a reordered list is a permanent diff. |
| `authorization_flow_slug` | string | `default-provider-authorization-implicit-consent` | See hardening notes. |
| `invalidation_flow_slug` | string | `default-provider-invalidation-flow` | |
| `signing_key_name` | string | `authentik Self-signed Certificate` | Signs OIDC tokens and SAML assertions. |
| `oauth2_scope_mappings` / `saml_property_mappings` | list(string) | stock managed IDs | Order matters (it is the order the API stores). Per-provider override available. |
| `custom_scope_mappings` | map(object) | `{}` | Scope mappings this module authors; referenced as `custom:<key>`. |

Users are never managed: only group membership is. Every username referenced in
`groups[*].users` must already exist.

## Custom scope mappings

`oauth2_scope_mappings` and a provider's `scope_mappings` hold two kinds of
entry, in one ordered list because order is what the API stores:

- a **managed id** (`goauthentik.io/providers/oauth2/scope-email`) — a mapping
  authentik's blueprints own, read through a data source and never modified;
- **`custom:<key>`** — an entry of `custom_scope_mappings`, authored by this
  module.

The custom kind exists because authentik's built-in mappings are blueprint
objects the server restores on upgrade, so a claim it does not emit by default
cannot be added by editing one. The usual case is an application that refuses a
login whose ID token lacks something stock authentik does not assert — a `true`
`email_verified`, a groups claim shaped for one app. Give the replacement the
same `scope_name` as the built-in it stands in for and leave the built-in out of
that provider's list; providers that do not name it keep the stock mapping.

An `expression` runs server-side on every token issue: keep it side-effect free,
and never put a credential in one (any authentik admin can read the body).

## Destroy protection

Applications, all three provider kinds, groups, the custom scope mappings and
the embedded outpost carry `lifecycle { prevent_destroy = true }`. Destroying
any of them is a live outage — a slug *is* the OIDC issuer path, a group
destroy takes its memberships and every binding pointing at it, and the outpost
is authentik's own object. Policy bindings are deliberately exempt: they are
cheap to recreate, and they are the supported way to widen or narrow access.

It is unconditional, not a per-object flag, and that is the deliberate
difference from `cloudflare-zone`: a flag has to route the object to a second
resource address, and an address change here plans as the destroy+create the
flag exists to prevent.

### Removing an object

`prevent_destroy` cannot be switched off from the caller, so removal is:

```bash
terraform state rm 'module.sso.authentik_application.this["retired-app"]'
# then delete the map entry, and delete the object in authentik itself
```

Renaming a map key is the same operation in disguise (destroy + create) — use a
`moved {}` block instead, which `prevent_destroy` does not block.

## Outputs

`application_ids`, `application_uuids`, `group_ids`, `oauth2_provider_ids`,
`oauth2_client_ids`, `proxy_provider_ids`, `saml_provider_ids`,
`custom_scope_mapping_ids`, `policy_binding_ids`.

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
module therefore **rejects a regex redirect URI containing an unescaped dot**.
In an HCL quoted string `\.` is an invalid escape sequence, so double the
backslash — `url = "https://app\\.example\\.com/callback"` — which is the
single-backslash pattern authentik stores. Prefer `strict` unless a wildcard is
genuinely required.

**Consent flow.** The default authorization flow is authentik's
implicit-consent flow: an already-signed-in user is redirected without a consent
prompt. Set `authorization_flow_slug =
"default-provider-authorization-explicit-consent"` if you want a first-use
consent screen per application (each user sees it once per app).

**Unbound applications fail the plan.** An application no `policy_bindings`
entry names is reachable by every authenticated user, and forgetting one
otherwise produces a perfectly valid plan. Each `authentik_application` therefore
carries a `precondition` asserting it is bound — a precondition rather than a
`check` block, because check assertions are only warnings and this has to fail
the plan, including a read-only drift-plan job. A tile that really is open to
everyone declares `allow_unbound = true`.

Only `enabled` bindings satisfy the check: the policy engine never evaluates a
binding with `enabled = false`, so suspending an application's only binding
would otherwise widen it to every authenticated user with a green plan. A
binding with `negate = true` does still satisfy it — under the default
`policy_engine_mode = "any"` a deny-list-only application is reachable by
everyone outside the named group, so gate such an app with an allow binding as
well, or declare it with `allow_unbound = true`.

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
after every apply that creates objects (the `policy_binding_ids`,
`custom_scope_mapping_ids` and `*_provider_ids` outputs give you the
identifiers) rather than assuming an import file written at adoption time still
covers everything.

### Migrating an existing root module onto this one

State that already holds the resources needs `moved {}`, not `import` — the
objects stay put and only their addresses change:

```hcl
moved {
  from = authentik_application.app["grafana"]
  to   = module.sso.authentik_application.this["grafana"]
}
```

Write one block per **instance**, not per resource, and check the list against
`terraform state list` before planning: an address with no block plans as
destroy+create of a live SSO object. `prevent_destroy` does not block a move,
and re-applying is a no-op once state carries the new addresses. Keep any
`import` blocks (and any import script) pointing at the new addresses in the
same change — the two files describe the same identities and are the disaster
recovery path.

## Tests

```bash
cd terraform/modules/authentik-sso
terraform init -backend=false
terraform test
```

`tests/validation.tftest.hcl` covers every variable validation and every
precondition — the unbound-application guardrail, the four cross-map reference
checks and the scope-mapping resolution. `terraform validate` evaluates no
caller values, so it runs none of them; the runs are plan-only against a
`mock_provider`, so they need no credentials and create no state. CI runs the
same command through `ci/validate/terraform.yml` with `test: true`.
