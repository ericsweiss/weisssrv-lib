# weisssrv-lib documentation

- [INCLUDE-CONTRACT.md](INCLUDE-CONTRACT.md) — how to `include:` each CI
  template, its `spec:inputs` with defaults, and the weisssrv parity note per
  template.
- [SCRIPTS.md](SCRIPTS.md) — the `scripts/` contract: what each gate/generator
  does, its flags, and the consumer config file it reads (examples in
  [../examples/](../examples/README.md)).
- [VERSIONING.md](VERSIONING.md) — what one tag covers, the conventional-commit
  release automation, and the consumer upgrade flow.

Each Terraform module documents its own inputs, outputs and consumption pattern:
[cloudflare-zone](../terraform/modules/cloudflare-zone/README.md),
[tailscale-acl](../terraform/modules/tailscale-acl/README.md),
[authentik-sso](../terraform/modules/authentik-sso/README.md).

The top-level [../README.md](../README.md) is the overview + repo map. The CLI
has its own [../cli/README.md](../cli/README.md).
