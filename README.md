# weisssrv-lib

Shared CI templates, Ansible roles, Terraform module shapes, lint
configurations, helper scripts, taskfile fragments, and the project CLI for the
weisssrv family. One source of truth for the layer every repo in that family
shares — a lint/version/build change is made once here and pulled in by each
consumer at a pinned tag.

## Current release

**v0.9.4.** Every pin example on this page and in `docs/` is written as
`<CURRENT_TAG>`; substitute the release you are adopting, so a release bump
touches the few copy-paste snippets that must be runnable rather than a dozen
stale examples. This line is the authority for the literal; the runnable
snippets that repeat it (`cli/README.md`, `docker/README.md`, the three
Terraform module READMEs) are held equal to it by
`tests/test_release_version.py`, and `docs/VERSIONING.md` carries the release
checklist.

## Who consumes it

| Consumer | What it is | What it pins |
| --- | --- | --- |
| [weisssrv](https://git.ericsweiss.com/eric/weisssrv) | the homelab platform repo — one cluster, running | CI template includes, the `weisssrv.infra` collection, vendored scripts |
| [weisssrv-app-template](https://git.ericsweiss.com/eric/weisssrv-app-template) | the tenant scaffold — repos that deploy *into* that cluster | CI template includes, vendored scripts, the CLI |
| [weisssrv-cluster-template](https://git.ericsweiss.com/eric/weisssrv-cluster-template) | the copier template a **new cluster** is generated from | CI includes, the collection, all 3 Terraform modules — all through one `lib_ref` answer |

Each consumer records its own pin sites and vendored copies — the library
knows nothing about who pins it. What it publishes instead is the offer list
([scripts/vendorable-paths.yml](scripts/vendorable-paths.yml)) of paths a
consumer may vendor, gated consumer-side by a `scripts/vendored-manifest.yml`
each consumer owns (see `scripts/check-vendored-copies.py`).

## What's here

```
ci/            GitLab CI templates (include:project + spec:inputs)
  lint/        yaml-lint, shellcheck (incl. *.sh.j2 neutralizer), docs-link-check,
               python-lint (ruff), ansible-lint
  validate/    flux-lint (kustomize+kubeconform, substitute toggle),
               terraform (fmt + validate, two jobs)
  security/    secret-detection (GitLab managed job + gitleaks ruleset override)
  build/       docker-build (the DinD build_and_push helper, parameterized)
  test/        python-tests (pytest + junit)
  review/      pr-agent (AI review)
  release/     semantic-release (auto tag + GitLab Release from conventional
               commits) + github-release-workflow.example.yml (Actions reference
               copy, vendored not included)
  maintenance/ version-check (read-only report) + version-bump-bot (one bump MR,
               never auto-merged)
  deploy/      the Ansible deploy toolchain: deploy-base, kubectl-setup,
               ansible-deploy — shipped and versioned, only deploy-base adopted
  github/      ci.example.yml + build-image.example.yml (Actions reference
               copies, vendored not included)
  templates/   shared fragments: dep-cache, install-1password,
               terraform-http-backend
  internal/    this library's own pipeline wiring (molecule child pipeline) —
               not a consumer contract
ansible_collections/weisssrv/infra/
               every host-configuration role, consumed by FQCN
               (weisssrv.infra.<role>)
terraform/
  modules/     reusable module shapes: cloudflare-zone, tailscale-acl,
               authentik-sso
lint/          shared config files (two yamllint profiles, gitleaks + GitLab
               ruleset, ruff, editorconfig, pre-commit) — see lint/README.md
scripts/       the gates + generators CI jobs run: version tracking, deploy/
               molecule coverage, Flux + Prometheus checks, doc links, the
               release automation — see docs/SCRIPTS.md
taskfiles/     go-task include fragments (lint, flux) so `task lint` mirrors CI
               — see taskfiles/README.md
docker/        published per release: the two molecule test/CI images for the
               collection + ansible-deploy (the pre-baked deploy job image)
examples/      copy-and-edit config files for the helper scripts
cli/           weisssrv-new-project — the copier wrapper that renders the
               cluster template (new-cluster) and the app template (new-app)
docs/          the include contract, the scripts contract, versioning policy,
               the extensibility seam map
tests/         pytest for scripts/ (the CLI has its own cli/tests/)
```

## Using it

Consumers pin a **release tag** and include a template by file path:

```yaml
include:
  - project: eric/weisssrv-lib
    ref: <CURRENT_TAG>
    file: /ci/lint/yaml-lint.yml
    inputs:
      tags: []                     # tag-less shared runner (tenant default)
      config: "-c .yamllint"
      targets: "."
```

The library is **internal** visibility, so any authenticated instance user (and
therefore every repo `eric` owns) can resolve `include: project:`.
`include: project:` is resolved with the pipeline creator's read access at
pipeline-creation time — not `CI_JOB_TOKEN` — so it "just works" for
eric-triggered pipelines.

Every CI template's inputs, defaults, and consumer wiring are documented in
[docs/INCLUDE-CONTRACT.md](docs/INCLUDE-CONTRACT.md); the helper scripts those
jobs run — and the config file each reads its site data from — are in
[docs/SCRIPTS.md](docs/SCRIPTS.md). The tag/version policy is in
[docs/VERSIONING.md](docs/VERSIONING.md).

### Three consumer profiles

Every template's input defaults target the first profile; the other two are
reached by passing inputs, never by forking a job.

- **weisssrv** (privileged `infrastructure` runner, root): the defaults *are*
  weisssrv's values — runner tag `infrastructure`, its tool pins, `flux-lint`
  `substitute: true`. Adopting a template with defaults reproduces what
  weisssrv already ran; the per-template parity note in the include contract
  records where that is not literally true and what to pass instead.
- **tenant repos** (shared non-privileged `k8s-deploy` runner, non-root, UID
  1000): pass `tags: []` and the non-root-safe input set (`substitute: false`,
  literal image pins, empty `apt_packages`). `docker-build` needs a privileged
  runner and is therefore weisssrv-only unless the tenant registers its own.
- **a generated cluster** (weisssrv-cluster-template's output): a *new*
  platform repo, so it consumes the widest surface — CI includes, the Ansible
  collection, and all three Terraform modules — but it is **pre-bootstrap** on
  day one. That changes two things: `flux-lint` must be passed
  `require_cluster_root: false` until `flux bootstrap` has written the gotk
  files, and every pin (includes, `requirements.yml`, module sources) comes from
  a single copier answer, `lib_ref`, rather than from hand-edited literals. The
  generated repo runs on whichever runner its own instance provides, so it
  passes `tags` explicitly rather than inheriting weisssrv's.

## Terraform modules

`terraform/modules/` ships the generic shape of the three external-state
modules a cluster needs — `cloudflare-zone` (DNS + zone settings),
`tailscale-acl` (tailnet policy + Split-DNS) and `authentik-sso` (SSO objects).
Consumers pin a tag on the module source and pass their own site data:

```hcl
module "zone" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/cloudflare-zone?ref=<CURRENT_TAG>"

  account_id = var.cloudflare_account_id
  zone_name  = var.external_domain
  records    = { ... }
}
```

Provider blocks, backends and lockfiles stay in the consuming root module. Each
module's README documents its inputs, outputs and guardrails; the summary is in
[docs/INCLUDE-CONTRACT.md](docs/INCLUDE-CONTRACT.md).

## Ansible collection

`ansible_collections/weisssrv/infra/` holds the host-configuration roles — base
hardening, storage, DNS/SMTP, k3s, Proxmox guest lifecycle, exporters, and the
application guests. Consumers install it from git at a pinned tag and address
the roles by FQCN, so an upgrade is a one-line, reviewable ref bump:

```yaml
# ansible/requirements.yml
collections:
  - name: git+https://git.ericsweiss.com/eric/weisssrv-lib.git#/ansible_collections/weisssrv/infra
    type: git
    version: <CURRENT_TAG>
```

```yaml
- hosts: nas
  roles:
    - role: weisssrv.infra.nas_storage
```

Site data (domains, IPs, pool names) is passed in — never baked into a role
default. The role table and the inventory-wide alias table are in the
[collection README](ansible_collections/weisssrv/infra/README.md); per-role
variables are in each role's own README; the old → new rename map for adopting
the collection is
[MIGRATING.md](ansible_collections/weisssrv/infra/MIGRATING.md).

## The CLI

`cli/` ships `weisssrv-new-project`, a copier wrapper: `new-cluster` renders the
**cluster** template and `new-app` the **app (tenant)** template into a new
repo, validating source and destination up front because copier's own failure
modes are late and messy. The package is stdlib-only and offline; copier is an
optional extra. Install it at a pinned tag — the spec is positional, not
`--spec`:

```bash
pipx install 'git+https://git.ericsweiss.com/eric/weisssrv-lib.git@<CURRENT_TAG>#subdirectory=cli'
# rendering (either subcommand): add the copier extra
pipx install 'weisssrv-lib-cli[cluster] @ git+https://git.ericsweiss.com/eric/weisssrv-lib.git@<CURRENT_TAG>#subdirectory=cli'
```

See [cli/README.md](cli/README.md).

## Local gates

Run these before opening an MR. This is the canonical list — `AGENTS.md` points
here rather than restating it.

```bash
python3 -m pytest tests cli/tests -q     # scripts + CLI tests
# same target set the pipeline's yaml-lint job passes
yamllint -c .yamllint ci/ lint/ taskfiles/ ansible_collections/ .gitlab/ .gitlab-ci.yml
shellcheck --severity=warning --exclude=SC1091,SC2034 scripts/*.sh
ruff check --config lint/ruff.toml scripts tests cli examples
gitleaks detect --no-git --config lint/gitleaks.toml   # what CI's secret_detection runs
# YAML smoke over every CI template (tests/test_render_templates.py does the
# full render; `!reference` needs a loader that knows the tag)
python3 -c "import glob,yaml; L=type('L',(yaml.SafeLoader,),{}); L.add_constructor('!reference', lambda l,n: l.construct_sequence(n)); [list(yaml.load_all(open(f),Loader=L)) for f in glob.glob('ci/**/*.yml',recursive=True)]"
# collection changes only:
ANSIBLE_COLLECTIONS_PATH=$PWD:~/.ansible/collections \
  ansible-lint ansible_collections/weisssrv/infra/roles/*
# the collection's own shell, which CI reaches via find_dir: ansible_collections.
# The *.sh.j2 half needs the template's Jinja neutralizer, so only CI covers it.
find ansible_collections -name '*.sh' -print0 \
  | xargs -0 shellcheck --severity=warning --exclude=SC1091,SC2034
```

The library's own pipeline (`.gitlab-ci.yml`) runs those by **including its own
templates** (`include: local:`), so every template is rendered and executed by
the MR that changes it, plus a YAML-parse smoke over every CI template. Keep it
that way: a new job here should be a new template plus an include, not an inline
job. Three templates have no library-side workload and are still first rendered
in a consumer — `ci/validate/flux-lint.yml`, the `ci/templates/` fragments, and
`ci/maintenance/version-bump-bot.yml`; the header comment in `.gitlab-ci.yml`
carries the reason for each. Merging to `main` runs
`ci/release/semantic-release.yml`, which cuts the tag consumers pin.

## Scope

This library carries the **building blocks** — anything a second cluster or
tenant could reuse: CI templates, Ansible roles, Terraform module shapes, helper
scripts, lint profiles, the CLI. A cluster is assembled from these blocks by
[weisssrv-cluster-template](https://git.ericsweiss.com/eric/weisssrv-cluster-template);
`weisssrv` is one instantiation of it.

What deliberately stays out: site data of every kind (domains, IPs, hostnames,
pool names, credentials — those are inputs), Kubernetes manifests (they live in
the cluster template so a cluster is self-contained, with no remote kustomize
bases), and weisssrv's own pipeline glue (`validation-gate`, its hand-written
per-playbook deploy jobs, `repo-sync`/`repo-policy` checks). The reusable half
of that deploy layer **is** here, as `ci/deploy/*` — shipped and versioned, with
only `deploy-base` adopted so far. There is **no
Renovate** anywhere — version bumps come from
`ci/maintenance/version-bump-bot.yml`, which each CONSUMER schedules against its
own version-check command and config. This library ships the template but does
not run it on itself: it tracks no upstream versions of its own.

A consumer whose backends differ from weisssrv's — Ceph instead of ZFS, a
secrets store other than 1Password, GitHub instead of GitLab — is meant to be
served by the same tag. Roles that *are* a backend are skipped and replaced by a
sibling family in the same flat FQCN namespace; roles that merely use one carry
a seam variable defaulting to today's behaviour, and the CI scripts keep their
forge calls behind a `--platform` flag. The seam map and the contract for adding
an alternative are in [docs/EXTENSIBILITY.md](docs/EXTENSIBILITY.md).

The application-guest roles (`gitlab`, `plex`, `nextcloud`, `immich`,
`immich_ml`, `home_assistant`) **are** in scope as of this release. They were
held back as "eric-specific" while they still carried site data in their
defaults; every domain, IP and credential is now an asserted input, so a second
cluster can run them unchanged.

Full per-item detail, including which weisssrv job each template reproduces, is
in [docs/INCLUDE-CONTRACT.md](docs/INCLUDE-CONTRACT.md).
