# Terraform modules

One module per provider: [`cloudflare-zone`](cloudflare-zone/README.md),
[`tailscale-acl`](tailscale-acl/README.md),
[`authentik-sso`](authentik-sso/README.md),
[`unifi-network`](unifi-network/README.md). Each module's own README is the
reference for its inputs and behaviour; this page holds the three policies they
share.

## The `required_version` floor is the test harness's

Every module ships `tests/validation.tftest.hcl`, and a module's `terraform`
block constrains both the tests and every caller. The floor is therefore set by
whichever the tests need, not by the configuration:

| Need | Floor |
|---|---|
| `optional()` attribute defaults and `import` blocks (every module's configuration) | 1.5 |
| `mock_provider` and `override_data` in the tests | 1.7 |
| Cross-variable validation in a configuration | 1.9 |
| `override_during = plan` in the tests | 1.11 |

A module is pinned to the highest floor it actually uses, so a consumer on an
older Terraform is held to that module's test harness. Floors therefore differ
between modules: `authentik-sso` is 1.11 for `override_during = plan`, the other
three are 1.7. Raise one only when that module's own configuration or tests need
the feature, never to keep the four in step. Raising a floor is breaking for a
consumer on an older Terraform.

## Pre-1.0 providers are pinned to the minor

A pre-1.0 provider can break shapes in a minor release, so those are pinned with
all three components (`~> 0.55.0`). `~> 0.55` would float every 0.x minor —
`~>` only pins everything left of the last component. The caller's lockfile pins
the exact build; treat a minor bump as its own change and re-read the release
notes for schema moves.

## Validation regexes are written out per validation

Variable validation cannot reference `locals` at the current floor and Terraform
has no user-defined functions, so a shared regex (the port-list and bare-IPv4
forms in `unifi-network`) is repeated in each validation that needs it. Edit
every copy together. Hoist them into `locals` if the floor ever moves to 1.9 or
later — do not raise the floor for this alone.
