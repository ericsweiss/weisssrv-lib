# weisssrv-lib

Shared CI templates, Ansible roles, Terraform module shapes, lint
configurations, helper scripts, taskfile fragments, and the project CLI
consumed by
[weisssrv](https://git.ericsweiss.com/eric/weisssrv) and by projects generated
from
[weisssrv-app-template](https://git.ericsweiss.com/eric/weisssrv-app-template).

The goal is one source of truth for the generic CI/tooling layer both a homelab
platform repo and its cluster tenants share — so a lint/version/build change is
made once here and pulled in by each consumer at a pinned tag.

## What's here

```
ci/            GitLab CI templates (include:project + spec:inputs)
  lint/        yaml-lint, shellcheck (incl. *.sh.j2 neutralizer), docs-link-check, python-lint (ruff)
  validate/    flux-lint (kustomize+kubeconform, substitute toggle), terraform (fmt+validate)
  security/    secret-detection (gitleaks passthrough)
  build/       docker-build (the DinD build_and_push helper, parameterized)
  test/        python-tests (pytest + junit)
  review/      pr-agent (AI review)
  release/     semantic-release (auto tag + GitLab Release from conventional commits)
  maintenance/ version-bump-bot (one scheduled bump MR, never auto-merged)
  templates/   shared fragments: dep-cache, install-1password, terraform-http-backend
  internal/    this library's own pipeline wiring (molecule child pipeline) — not a consumer contract
ansible_collections/weisssrv/infra/
               the generic Ansible roles, consumed by FQCN (weisssrv.infra.<role>)
terraform/
  modules/     reusable module shapes: cloudflare-zone, tailscale-acl, authentik-sso
lint/          shared config files (.yamllint profiles, gitleaks + ruleset, ruff, editorconfig, pre-commit)
scripts/       the gates + generators CI jobs run: version tracking, deploy/molecule
               coverage, Flux + Prometheus checks, doc links — see docs/SCRIPTS.md
taskfiles/     go-task include fragments (lint, flux) so `task lint` mirrors CI
docker/        molecule test/CI images for the collection
examples/      copy-and-edit config files for the helper scripts
cli/           weisssrv-new-project — tenant scaffolding (rename/prune/wire/verify)
               plus new-cluster, the experimental copier wrapper
docs/          the include contract, the scripts contract, versioning policy
tests/         pytest for scripts/ (the CLI has its own cli/tests/)
```

## Using it

Consumers pin a **release tag** and include a template by file path:

```yaml
include:
  - project: eric/weisssrv-lib
    ref: v0.2.0
    file: /ci/lint/yaml-lint.yml
    inputs:
      tags: []                     # tag-less shared runner (tenant default)
      config: "-c .yamllint"
      targets: "."
```

The library is **internal** visibility, so any authenticated instance user (and
therefore every tenant repo `eric` owns) can resolve `include: project:`.
`include: project:` is resolved with the pipeline creator's read access at
pipeline-creation time — not `CI_JOB_TOKEN` — so it "just works" for
eric-triggered pipelines.

Every CI template's inputs, defaults, and consumer wiring are documented in
[docs/INCLUDE-CONTRACT.md](docs/INCLUDE-CONTRACT.md); the helper scripts those
jobs run — and the config file each reads its site data from — are in
[docs/SCRIPTS.md](docs/SCRIPTS.md). The tag/version policy is in
[docs/VERSIONING.md](docs/VERSIONING.md).

### Two consumer profiles

- **weisssrv** (privileged `infrastructure` runner, root): the templates default
  to weisssrv's current values (runner tag `infrastructure`, its tool pins,
  `flux-lint` `substitute: true`). Adopting a template with defaults reproduces
  weisssrv's current behavior — see the parity notes per template in the include
  contract.
- **tenant repos** (shared non-privileged `k8s-deploy` runner, non-root, UID
  1000): pass `tags: []` and the non-root-safe inputs (`substitute: false`,
  literal image pins). The `docker-build` template needs a privileged runner and
  is therefore weisssrv-only unless a tenant registers its own.

## Terraform modules

`terraform/modules/` ships the generic shape of the three external-state
modules a cluster needs — `cloudflare-zone` (DNS + zone settings),
`tailscale-acl` (tailnet policy + Split-DNS) and `authentik-sso` (SSO objects).
Consumers pin a tag on the module source and pass their own site data:

```hcl
module "zone" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/cloudflare-zone?ref=v0.2.0"

  account_id = var.cloudflare_account_id
  zone_name  = var.external_domain
  records    = { ... }
}
```

Provider blocks, backends and lockfiles stay in the consuming root module. Each
module's README documents its inputs, outputs and guardrails; the summary is in
[docs/INCLUDE-CONTRACT.md](docs/INCLUDE-CONTRACT.md).

## Ansible collection

`ansible_collections/weisssrv/infra/` holds the generic host-configuration
roles (base hardening, storage, DNS/SMTP, k3s, Proxmox guests, exporters, …).
Consumers install it from git at a pinned tag and address the roles by FQCN, so
an upgrade is a one-line, reviewable ref bump:

```yaml
# ansible/requirements.yml
collections:
  - name: git+https://git.ericsweiss.com/eric/weisssrv-lib.git#/ansible_collections/weisssrv/infra
    type: git
    version: v0.2.0
```

```yaml
- hosts: nas
  roles:
    - role: weisssrv.infra.nas_storage
```

Site data (domains, IPs, pool names) is passed in — never baked into a role
default. Per-role variables are in each role's README; the consumption details,
including how to point Ansible at an unmerged checkout, are in
[docs/INCLUDE-CONTRACT.md](docs/INCLUDE-CONTRACT.md).

## The scaffolding CLI

`cli/` ships `weisssrv-new-project`, which turns a fresh copy of the app
template into a configured project: `rename` the placeholders (optionally
selecting the CI shape in the same call with `--ci gitlab|github|none`), `prune`
components you don't need — including the CI shapes you didn't pick, via
`prune ci:<shape>` — `wire` opt-in components, and `verify` the result.
`new-cluster` (experimental) additionally renders a **cluster** template with
copier. Install it at a pinned tag — the spec is positional, not `--spec`:

```bash
pipx install 'git+https://git.ericsweiss.com/eric/weisssrv-lib.git@v0.2.0#subdirectory=cli'
# new-cluster only: add the copier extra
pipx install 'weisssrv-lib-cli[cluster] @ git+https://git.ericsweiss.com/eric/weisssrv-lib.git@v0.2.0#subdirectory=cli'
```

See [cli/README.md](cli/README.md).

## Development

```bash
python3 -m pytest tests cli/tests -q     # scripts + CLI tests
# CLI only: also diff the bundled scaffold fixture against the real app template
WEISSSRV_TEMPLATE_ROOT=~/src/weisssrv-app-template python3 -m pytest cli/tests -q
yamllint -c .yamllint ci/ lint/ taskfiles/ .gitlab-ci.yml
shellcheck --severity=warning --exclude=SC1091,SC2034 scripts/*.sh
ruff check --config lint/ruff.toml scripts tests cli
gitleaks detect --no-git --config lint/gitleaks.toml   # what CI's secret_detection runs
```

The library's own pipeline (`.gitlab-ci.yml`) runs those by **including its own
templates** (`include: local:`), so every template is rendered and executed by
the MR that changes it, plus a YAML-parse smoke over every CI template. Three
templates have no library-side workload and are still first rendered in a
consumer — `ci/validate/flux-lint.yml`, the `ci/templates/` fragments, and
`ci/maintenance/version-bump-bot.yml`; the header comment in `.gitlab-ci.yml`
carries the reason for each. Merging to `main` runs
`ci/release/semantic-release.yml`, which cuts the tag consumers pin.

## Scope

This library carries the **generic building blocks** — anything a second
cluster or tenant could reuse unchanged: CI templates, Ansible roles, Terraform
module shapes, helper scripts, lint profiles, the CLI. A cluster is assembled
from these blocks by
[weisssrv-cluster-template](https://git.ericsweiss.com/eric/weisssrv-cluster-template);
`weisssrv` is one instantiation of it.

What deliberately stays out: site data of every kind (domains, IPs, hostnames,
pool names, credentials — those are inputs), the eric-specific Ansible roles
(gitlab, plex, immich, nextcloud, home_assistant), Kubernetes manifests (they
live in the cluster template so a cluster is self-contained, with no remote
kustomize bases), and weisssrv's own pipeline glue (`validation-gate`, the
deploy/maintenance job matrix, `repo-sync`/`repo-policy` checks). There is **no
Renovate** anywhere — version bumps come from
`ci/maintenance/version-bump-bot.yml`, which each CONSUMER schedules against its
own version-check command and config. This library ships the template but does
not run it on itself: it tracks no upstream versions of its own.

Full per-item detail, including which weisssrv job each template reproduces, is
in [docs/INCLUDE-CONTRACT.md](docs/INCLUDE-CONTRACT.md).
