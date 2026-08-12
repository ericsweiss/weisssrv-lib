# weisssrv-lib documentation

- [INCLUDE-CONTRACT.md](INCLUDE-CONTRACT.md) — how to `include:` each CI
  template, its full `spec:inputs` set with defaults, and the parity note per
  template; plus the Terraform-module and Ansible-collection consumption
  patterns.
- [SCRIPTS.md](SCRIPTS.md) — the `scripts/` contract: what each gate/generator
  does, its flags and env, and the consumer config file it reads (examples in
  [../examples/](../examples/README.md)).
- [VERSIONING.md](VERSIONING.md) — what one tag covers, the conventional-commit
  release automation, the protected-tag prerequisite, the full pin surface a
  consumer bump has to walk, and this repo's release checklist.
- [EXTENSIBILITY.md](EXTENSIBILITY.md) — how a consumer whose backends are not
  weisssrv's (Ceph, a non-1Password secrets store, GitHub) adopts the library:
  the seam map, what is backend-specific by design, and the contract for adding
  an alternative.
- [CONSUMERS.yml](CONSUMERS.yml) — the registry of every repo that pins this
  library and every place it holds a pin. Read it before cutting a release.

Vendored surfaces document themselves next to the files:
[../lint/README.md](../lint/README.md) (linter configs) and
[../taskfiles/README.md](../taskfiles/README.md) (go-task fragments) both state
the copy-into-consumer contract.

The Ansible collection has its own front door —
[../ansible_collections/weisssrv/infra/README.md](../ansible_collections/weisssrv/infra/README.md)
(role table, inventory-wide aliases, testing) — and its own migration map,
[MIGRATING.md](../ansible_collections/weisssrv/infra/MIGRATING.md).

Each Terraform module documents its own inputs, outputs and consumption pattern:
[cloudflare-zone](../terraform/modules/cloudflare-zone/README.md),
[tailscale-acl](../terraform/modules/tailscale-acl/README.md),
[authentik-sso](../terraform/modules/authentik-sso/README.md).

The top-level [../README.md](../README.md) is the overview + repo map, and names
the current release. The CLI has its own [../cli/README.md](../cli/README.md);
the published images (the two molecule ones and `ansible-deploy`) are documented
in [../docker/README.md](../docker/README.md).
