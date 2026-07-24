# weisssrv-lib

Shared CI templates, lint configurations, helper scripts, taskfile fragments,
and the project CLI consumed by
[weisssrv](https://git.ericsweiss.com/eric/weisssrv) and by projects generated
from
[weisssrv-project-template](https://git.ericsweiss.com/eric/weisssrv-project-template).

The goal is one source of truth for the generic CI/tooling layer both a homelab
platform repo and its cluster tenants share — so a lint/version/build change is
made once here and pulled in by each consumer at a pinned tag.

## What's here

```
ci/            GitLab CI templates (include:project + spec:inputs)
  lint/        yaml-lint, shellcheck (incl. *.sh.j2 neutralizer), docs-link-check
  validate/    flux-lint (kustomize+kubeconform, substitute toggle), terraform (fmt+validate)
  security/    secret-detection (gitleaks passthrough)
  build/       docker-build (the DinD build_and_push helper, parameterized)
  test/        python-tests (pytest + junit)
  templates/   shared fragments: dep-cache, install-1password, terraform-http-backend
lint/          shared config files (.yamllint profiles, gitleaks + ruleset, editorconfig, pre-commit)
scripts/       stdlib helpers (check-doc-links, check-taskfile, flux-render, kubeconform-skipped)
taskfiles/     go-task include fragments (lint, flux) so `task lint` mirrors CI
cli/           weisssrv-new-project — the tenant scaffolding CLI (rename/prune/wire/verify)
docs/          the include contract + versioning policy
tests/         pytest for scripts/ (the CLI has its own cli/tests/)
```

## Using it

Consumers pin a **release tag** and include a template by file path:

```yaml
include:
  - project: eric/weisssrv-lib
    ref: v0.1.0
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
[docs/INCLUDE-CONTRACT.md](docs/INCLUDE-CONTRACT.md). The tag/version policy is
in [docs/VERSIONING.md](docs/VERSIONING.md).

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

## The scaffolding CLI

`cli/` ships `weisssrv-new-project`, which turns a fresh copy of the template
into a configured project: `rename` the placeholders, `prune` components you
don't need, `wire` opt-in components, and `verify` the result. See
[cli/README.md](cli/README.md).

## Development

```bash
python3 -m pytest tests cli/tests -q     # scripts + CLI tests
yamllint -c .yamllint ci/ lint/ taskfiles/
shellcheck --severity=warning --exclude=SC1091,SC2034 scripts/*.sh
```

The library's own pipeline (`.gitlab-ci.yml`) runs those plus a YAML-parse smoke
over every CI template.

## Scope

This library carries only the **generic** layer that template-derived projects
use. Ansible/molecule machinery, the deploy/maintenance jobs, `validation-gate`,
the molecule matrix + generator, `repo-sync`/`repo-policy` checks, `ansible-lint`,
`version-check`, and the drift-plan modules stay in weisssrv. There is **no
Renovate** anywhere.
