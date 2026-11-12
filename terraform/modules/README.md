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
| `override_during = plan` in the tests, write-only arguments in a configuration | 1.11 |

A module is pinned to the highest floor it actually uses, so a consumer on an
older Terraform is held to that module's test harness. Floors therefore differ
between modules: `authentik-sso` (`override_during = plan`) and `unifi-network`
(write-only `passphrase_wo`, and `override_during = plan` so its `check` blocks
evaluate in tests) are 1.11, the other two are 1.7. Raise one only when that
module's own configuration or tests need
the feature, never to keep the four in step. Raising a floor is breaking for a
consumer on an older Terraform.

The CLI that renders those floors in CI is `ci/validate/terraform.yml`'s `image`
input, digest-pinned to the release the consumers plan with. A raised floor
needs an image at or above it; the image does not set the floor.

## Pre-1.0 providers are pinned to the minor

A pre-1.0 provider can break shapes in a minor release, so those are pinned with
all three components (`~> 0.65.0`). `~> 0.65` would float every 0.x minor —
`~>` only pins everything left of the last component. The caller's lockfile pins
the exact build; treat a minor bump as its own change and re-read the release
notes for schema moves.

## Validation regexes are written out per validation

Terraform has no user-defined functions, so `unifi-network` repeats a shared
regex, such as the port-list and bare-IPv4 forms, in each validation that needs
it; each validation stays readable on its own. Edit every copy together.
