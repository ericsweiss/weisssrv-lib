# Include contract

How to consume each CI template, its complete `spec:inputs` set with defaults,
and the parity note recording what it reproduces for which consumer. All
templates are included the same way — by project + pinned ref + file path, with
optional `inputs:`:

```yaml
include:
  - project: eric/weisssrv-lib
    ref: <CURRENT_TAG>     # a release TAG (see VERSIONING.md) — never a branch
    file: /ci/<area>/<template>.yml
    inputs:
      <name>: <value>
```

`<CURRENT_TAG>` is the placeholder convention across this repo's docs; the
current release is named once, in the [README](../README.md#current-release).

## Who includes what

Three consumers plus this library's own pipeline. Read this table before
changing an input default: a default is only "safe" relative to the pipelines
that take it. The **lib (self)** column is this repo's `.gitlab-ci.yml`, which
adopts its own templates through `include: local:` — so it is the first
pipeline a changed default breaks, and several of its entries take the defaults
verbatim (see [README § Local gates](../README.md#local-gates)).

Both templates are copier templates, so each has TWO pipelines: the one CI runs
on the template repo itself, and the one it RENDERS. The consumer columns below
are the **rendered** pipelines — that is the surface an input default reaches at
scale. Each template repo's own pipeline is its own record (its `.gitlab-ci.yml`
against the rendered one), and they are NOT the same set: the app template's
own pipeline includes `ci/test/python-tests.yml` (its copier-schema and render
suite) and neither `flux-lint` nor `docker-build`, the reverse of its tenant
column here.

| Template | lib (self) | weisssrv | app-template (tenant) | cluster-template (cluster) |
| --- | :-: | :-: | :-: | :-: |
| [`ci/lint/yaml-lint.yml`](#cilintyaml-lintyml) | ● | ● | ● | ● |
| [`ci/lint/shellcheck.yml`](#cilintshellcheckyml) | ● | ● | | ● |
| [`ci/lint/terraform-tflint.yml`](#cilintterraform-tflintyml) | | ○ | | ○ |
| [`ci/lint/docs-link-check.yml`](#cilintdocs-link-checkyml) | ● | ● | ● | ● |
| [`ci/lint/runbook-anchors.yml`](#cilintrunbook-anchorsyml) | | ○ | | ○ |
| [`ci/lint/comment-length.yml`](#cilintcomment-lengthyml) | ● | ● | ● | ● |
| [`ci/lint/python-lint.yml`](#cilintpython-lintyml) | ● | ● | ● | ● |
| [`ci/lint/ansible-lint.yml`](#cilintansible-lintyml) | ● | ● | | ● |
| [`ci/validate/terraform.yml`](#civalidateterraformyml) | ● | ● | | ● |
| [`ci/validate/terraform-drift-plan.yml`](#civalidateterraform-drift-planyml) | | ● | | ○ |
| [`ci/validate/cluster-drift-plan.yml`](#civalidatecluster-drift-planyml) | | ○ | | ○ |
| [`ci/validate/flux-lint.yml`](#civalidateflux-lintyml) | | ● | ● | ● |
| [`ci/security/secret-detection.yml`](#cisecuritysecret-detectionyml) | ● | ● | ● | ● |
| [`ci/test/python-tests.yml`](#citestpython-testsyml) | ● | ● | | ● |
| [`ci/build/docker-build.yml`](#cibuilddocker-buildyml) | ●‡ | | ●† | |
| [`ci/review/pr-agent.yml`](#cireviewpr-agentyml) | ● | ● | ● | ● |
| [`ci/release/semantic-release.yml`](#cireleasesemantic-releaseyml) | ● | | ● | ●* |
| [`ci/maintenance/version-check.yml`](#cimaintenanceversion-checkyml) | ● | ● | | ● |
| [`ci/maintenance/version-bump-bot.yml`](#cimaintenanceversion-bump-botyml) | | | | ● |
| [`ci/internal/molecule-matrix.gitlab-ci.yml`](#internal-ci-fragments-ciinternal) | ● | | | |
| [`ci/templates/{dep-cache,install-1password,terraform-http-backend}.yml`](#shared-fragments-citemplates) | | ● | | ● |
| [`ci/templates/docker-dind.yml`](#shared-fragments-citemplates) | | ● | ○ | |
| [`ci/deploy/deploy-base.yml`](#deploy-templates-cideploy) | | ● | | ● |
| [`ci/deploy/kubectl-setup.yml`](#deploy-templates-cideploy) | | ● | | ● |
| [`ci/deploy/ansible-deploy.yml`](#deploy-templates-cideploy) | | ○ | | ○ |
| [`ci/deploy/cluster-verify-base.yml`](#deploy-templates-cideploy) | | ○ | | ○ |

●‡ = three separate `docker-build` entries, one per published image
(molecule-ci, molecule-test, ansible-deploy).
●* = copier-gated on `enable_semantic_release` (cluster template only).
†  = copier-gated on `enable_image_build` (app template). The other 18
cluster-template entries and 8 app-template entries are unconditional; the app
template has no `enable_semantic_release` question, so its tenant always gets
the release job.
○ = extracted here, not yet adopted. Every ○ in the table is one of these:
weisssrv runs the runbook-anchors gate from its own consolidated gate job and
the cluster template runs no equivalent; the cluster template carries a local
`.terraform-drift-plan` rather than including this one; the tenant extends
`ci/build/docker-build.yml` directly instead of `ci/templates/docker-dind.yml`;
and no consumer takes `cluster-verify-base`, `cluster-drift-plan`,
`terraform-tflint` or the `ansible-deploy` job template yet. Until a consumer
adopts one, treat its defaults as free to change. weisssrv and the cluster
template both include `kubectl-setup` and `!reference` `.kubectl-setup`, so its
inputs are contract.

The app template renders a TENANT: no Ansible, no Terraform, no shell scripts
and no test suite of its own, which is why the shellcheck, terraform-tflint,
terraform, terraform-drift-plan, ansible-lint, ansible-deploy and python-tests
rows are blank for it. The other blanks are cluster-side or library-side
templates a tenant never includes. It runs
`flux-lint` once, over `kubernetes/flux`; its optional manifests are
copier-gated files, so they are either in that one build or not generated at
all.

weisssrv includes most of this library. Its `include:` block takes the three
`ci/templates/*` fragments at their defaults; every other entry passes its own
`inputs:` — `secret-detection` included, because `cpu_selector` has no default
and GitLab rejects a pipeline that omits it. Inputs bind per ENTRY and therefore
need an entry each (the block in weisssrv's `.gitlab-ci.yml` is the current
shape — this page deliberately keeps no count).

Outside weisssrv's rendered pipeline: `docker-build`, `semantic-release` and
`version-bump-bot` (weisssrv runs a local version-bump job and cuts no
releases), `ci/internal/molecule-matrix.gitlab-ci.yml`, which drives this
library's own per-role matrix, and the ○ rows above, which are extracted but not
yet adopted there. Everything else in this library is live in the cluster repo,
so treat an input-default change as consumer-visible there by default —
`pr-agent`, `python-lint` and `ansible-lint` included.

**A GitHub-hosted consumer includes nothing.** Actions has no equivalent of
`include: project:` for a private library, so such a consumer (the app
template's `ci_shape: github`) VENDORS workflows and re-vendors deliberately. The three
reference workflows this repo publishes for that are
`ci/release/github-release-workflow.example.yml`, `ci/github/ci.example.yml` and
`ci/github/build-image.example.yml` — see "GitHub workflow examples" below. The
scripts those workflows run are shared unchanged; which of them are
forge-coupled is in [SCRIPTS.md](SCRIPTS.md#forge-coupling).

## Conventions shared by every template

- **`tags` (array)** — runner tag(s). Default `["infrastructure"]` (weisssrv's
  privileged runner). Tenants and generated clusters pass `tags: []` for a
  shared tag-less runner.
- **`changes` (array)** — the path list used in the merge-request and
  post-merge rules. Every default is a literal, self-contained list stated in
  the template; there is no anchor in any consumer that it must match.
- **`default_branch` (string, default `main`)** — the branch the post-merge
  rule compares against, on every template that has such a rule (15 of them:
  yaml-lint, shellcheck, docs-link-check, runbook-anchors, comment-length,
  python-lint, ansible-lint, terraform-tflint, terraform, terraform-drift-plan,
  flux-lint, secret-detection, python-tests, version-check, docker-build).
  It must be a **literal** name. The value is interpolated into a quoted
  `rules:if` string, and GitLab does not expand variables inside quotes, so
  `$CI_DEFAULT_BRANCH` would be compared as literal text and never match — a
  consumer on `master`/`trunk` that left the default would get a job that
  silently stops running after merge. The default reproduces every current
  consumer's behaviour byte-for-byte.
- **The pod-resource inputs** are in [Resource inputs](#resource-inputs),
  which records which templates take them and what each default is.
- **The library pin is a literal release tag on every entry.** The rule and the
  reasons are in [The library pin](#the-library-pin).
- **Name a template by its FULL path** (`ci/templates/install-1password.yml`,
  not `install-1password.yml`), and never name a `ci/<dir>/<file>.yml` path this
  repo does not ship — not even as a "not provided" example. The
  `ci-templates-parse` job asserts that every such path on this page exists in
  the checkout, so both rules are machine-enforced.
- The rules shape for lint/validate/test jobs is fixed: `schedule → never`,
  `merge_request_event → changes`, `default_branch → changes`, `web`.
- **Every template that DEFINES a named job retries it on
  `runner_system_failure` / `scheduler_failure` (max 2).** On a quota-capped
  shared runner a pipeline's fan-out can burst past the namespace quota at
  pod-creation time; the retry turns that hard failure into throttling. Three
  classes are outside that claim: templates that define a **hidden fragment**
  set no retry, because the consumer's own job supplies it
  (`ci/templates/dep-cache.yml`, `install-1password.yml`,
  `terraform-http-backend.yml` and `ci/deploy/kubectl-setup.yml`); the **bridge**
  job in `ci/internal/molecule-matrix.gitlab-ci.yml` carries none (GitLab
  rejects `retry` on a bridge), though the plan job in the same file does; and
  the **GitHub Actions reference copies** (`ci/github/*.example.yml`,
  `ci/release/github-release-workflow.example.yml`) are not GitLab jobs at all.
  `ci/deploy/ansible-deploy.yml` inherits its retry from
  `ci/deploy/deploy-base.yml` via `extends: $[[ inputs.base ]]`, and that base
  retries on `runner_system_failure` only.
- **Five templates set `interruptible: false` on the job they define; no other
  template sets `interruptible` at all.** The complete set, and what a consumer
  may conclude from it, is the subsection below.

### Jobs that opt out of `interruptible`

A consumer that pairs `default: interruptible: true` with
`workflow: auto_cancel: on_new_commit: interruptible` cancels superseded
pipelines, but not the jobs in this table: a job's own `interruptible` wins over
the `default:`. This is the whole list, so a consumer's comment about it can be
checked here instead of guessed.

| Template | Why it must survive a supersede |
| --- | --- |
| `ci/release/semantic-release.yml` | Creates the next tag. Cancelling it mid-API-call loses the release. |
| `ci/maintenance/version-bump-bot.yml` | Force-pushes a branch and opens one MR. |
| `ci/build/docker-build.yml` | Pushes an image later jobs consume. |
| `ci/deploy/deploy-base.yml` | Deploys touch live infrastructure. |
| `ci/deploy/cluster-verify-base.yml` | Reports on a deploy that already happened. |

`ci/deploy/ansible-deploy.yml` inherits the value from its base rather than
setting it. The bridge in `ci/internal/molecule-matrix.gitlab-ci.yml` sets
`interruptible: true`, and like everything in `ci/internal/` it carries no input
or behaviour guarantee for consumers.
`tests/test_include_contract_interruptible.py` keeps this table and the
templates in step.

## Resource inputs

Every lint, validate, test, build and security template that defines a named job
takes `job_memory_limit`, `job_memory_request` and `job_cpu_request`, and emits
them as `KUBERNETES_MEMORY_LIMIT`, `KUBERNETES_MEMORY_REQUEST` and
`KUBERNETES_CPU_REQUEST`. That is 13 templates. Seven job-defining templates
take none of the three and run at the runner default:
`ci/deploy/ansible-deploy.yml`, `ci/maintenance/version-check.yml`,
`ci/maintenance/version-bump-bot.yml`, `ci/release/semantic-release.yml`,
`ci/review/pr-agent.yml`, `ci/validate/cluster-drift-plan.yml` and
`ci/validate/terraform-drift-plan.yml`. Passing one of the three to those is an
unknown-input failure at pipeline creation.

`ci/build/docker-build.yml` and `ci/templates/docker-dind.yml` additionally take
`service_memory_limit` (3Gi) and `service_memory_request` (512Mi) for the dind
service, sized independently of the job container.

The defaults are right-sized per job class so concurrent pipelines pack into the
runner namespace's `limits.memory` quota instead of every job costing the runner
default:

| Limit / request / CPU request | Templates |
| --- | --- |
| 512Mi / 128Mi / 100m | yaml-lint, shellcheck, docs-link-check, comment-length, runbook-anchors, terraform-tflint |
| 1Gi / 256Mi / 200m | python-lint, terraform |
| 1500Mi / 512Mi / 300m | ansible-lint, python-tests |
| 2Gi / 512Mi / 300m | flux-lint, secret-detection |
| 4Gi / 1Gi / 900m | docker-build |

Raise one per consumer only when the job really needs more. On a
Kubernetes-executor runner `memory_limit_overwrite_max_allowed` and
`cpu_request_overwrite_max_allowed` are the hard ceiling, so a larger value is
clamped or fails at pod creation, and a runner that allows no override ignores
the values entirely.

No template sets a CPU *limit*, repo-wide. CPU is compressible, so a limit buys
nothing but CFS throttling; the request is the scheduler reservation, sized so a
full concurrency burst still fits the runner nodes' allocatable CPU.

## The library pin

Every weisssrv-lib `include:` entry pins a literal release tag. GitLab resolves
`include:` at pipeline-CREATION time, before the same file's `variables:` block
exists, so `ref: $WEISSSRV_LIB_REF` does not resolve. A project or group CI/CD
variable IS readable there, but it moves the pin out of git: a library bump
would stop appearing in a diff and could not be reviewed or reverted as an MR.
A branch ref is forbidden for the same reason plus one worse — a branch deleted
after merge takes the include with it, and until then the pipeline's behaviour
can change with no commit in the consuming repo. `scripts/check-lib-pins.py`
enforces both.

## Self-application (this library's own pipeline)

The library consumes its OWN templates rather than hand-rolling equivalents, so
an undeclared input or a malformed render fails the MR that introduced it —
includes expand at pipeline creation regardless of any job's `changes:`.
Executing a rendered script is separately path-gated, which is why python-tests
and ansible-lint both carry `ci/**/*`: a template-only MR still runs those two
for real. `local:` resolves against the pipeline's own commit; a
`project:` + `ref:` self-include would resolve the ref, not the branch under
review.

**Two runner classes.** The lint/test/security/review jobs are TAG-LESS and
non-root safe (`pip install --user`, `python -m`, no apt, no `/usr/local/bin`
writes) and land on the shared runner. The image builds, the molecule matrix and
the release retag need privileged Docker-in-Docker and are tagged
`infrastructure`, which is a PROJECT runner: it must be enabled for the project
(Settings > CI/CD > Runners) or every tagged job sits pending.

The tag-less fan-out is shaped by `needs:` chains in `.gitlab-ci.yml` because
the shared runner's namespace quota admits roughly seven concurrent job pods
while the pipeline creates about twelve tag-less jobs; a burst past the quota
surfaces as `runner_system_failure` at pod creation. Re-measure before trusting
those numbers. A new tag-less job goes on one of the chains.

The release retag republishes each built image under the tag semantic-release
just cut, so a consumer pins images at the same ref as the templates. It pulls
this pipeline's `:<short-sha>` first and only then `:latest`, which is what
keeps it off a `:latest` another pipeline retagged mid-flight.

### Molecule jobs

The molecule job templates live in `.gitlab/ci/molecule-jobs.gitlab-ci.yml`, not
under `ci/`, because the generated MR child pipeline includes only that one file
and must be self-contained: no `!reference` to a `.gitlab-ci.yml` anchor, no
static matrix (the child emits its own), and every `needs:` optional, since
those upstream jobs do not exist in a child.

The DinD service carries `--insecure-registry=<in-cluster cache>`: the
pull-through cache is plaintext HTTP, network-fenced to the runner's namespace,
and every fresh DinD daemon needs the flag to reach it. Each use is
timeout-bounded with a direct-registry fallback, so a cache that is absent or
unreachable only loses the warm hit.

Retries are infra-class only. A failing test must not be masked, and a hung one
must fail fast rather than burn a second full timeout; container-start storms
under a full fan-out are absorbed by the in-job destroy+jitter loop in
`scripts/molecule-retry.sh`, which owns the attempt count and jitter window. The
job timeout is 30m because that wrapper retries molecule up to four times and
the heaviest scenarios run 8-11 minutes per attempt.

---

## ci/lint/yaml-lint.yml

- **Reproduces:** weisssrv `yaml-lint`; the same job in both templates.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `yaml-lint` |  |
| `stage` | `lint` |  |
| `image` | `python:3.11-slim` |  |
| `tags` | `["infrastructure"]` |  |
| `yamllint_version` | `1.38.0` |  |
| `config` | `-d relaxed` | the FULL argument, e.g. `-c lint/yamllint-relaxed.yml` |
| `require_config` | `true` | fail when `config` is left at the default while the repo ships a profile |
| `targets` | `ansible/ kubernetes/ .gitlab-ci.yml .gitlab/ci/` | space-separated; a missing one is skipped with a note |
| `default_branch` | `main` |  |
| `changes` | `["ansible/**/*", "kubernetes/**/*", ".gitlab-ci.yml", ".gitlab/ci/**/*"]` |  |

- **Parity:** defaults reproduce weisssrv's four `yamllint -d relaxed <target>`
  invocations (run as a loop over `targets`) and its rules verbatim.
- **A `targets` entry that does not exist is skipped with a note**, not a
  failure — a repo that lacks one of the default trees still passes. But if
  **no** target existed at all the job FAILS: a green job that linted nothing is
  exactly the silent pass this gate exists to prevent.
- **Config profile:** `lint/yamllint-relaxed.yml` ships here; vendor it and
  pass `-c <path>`. Keep it OFF the repo root: ansible-lint discovers a root
  `.yamllint` and swaps it in for its own yaml[*] rules, losing fix mode.
- **A vendored profile the job does not point at FAILS the job.** `-d relaxed`
  is yamllint's own profile, so leaving `config` at the default while the repo
  ships one lints under the wrong rules and still reports green. `require_config:
  false` opts out, for a repo whose profile serves only other tools.
- **Tenant:** `inputs: { tags: [], config: "-c lint/yamllint-relaxed.yml", targets: "." }`.

## ci/lint/shellcheck.yml

- **Reproduces:** weisssrv `shellcheck`, including the `*.sh.j2` Jinja
  neutralizer.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `shellcheck` |  |
| `stage` | `lint` |  |
| `image` | `koalaman/shellcheck-alpine:v0.10.0` |  |
| `tags` | `["infrastructure"]` |  |
| `severity` | `warning` |  |
| `exclude` | `SC1091,SC2034` |  |
| `direct_globs` | `scripts/*.sh ansible/*.sh` |  |
| `find_dir` | `ansible/roles` | empty or absent skips both find loops |
| `default_branch` | `main` |  |
| `changes` | `["scripts/**/*", "ansible/*.sh", "ansible/roles/**/*.sh", "ansible/roles/**/*.sh.j2"]` |  |

- **Parity:** the neutralizer logic (raw-wrapped vs plain templates, the
  rc-accumulating list-file loop) is extracted verbatim; defaults reproduce
  weisssrv's globs and find dir.
- **A `find_dir` that is empty OR absent skips both find loops with a note**, so
  a consumer that simply does not have that tree still passes.
- **All three blocks run, then the job decides.** A failure in the direct-glob
  block does not hide the role-shell and `*.sh.j2` results: each block folds its
  rc into a `failed` flag, and the final accounting block exits on it (after the
  `linted > 0` check).
- **Neutralizer constraint:** in a `{% raw %}`-wrapped template only FULL-LINE
  `{# … #}` comments are stripped (a comment-range delete would eat bash
  `${#arr[@]}`), so keep the out-of-raw header comments of such a template to
  single lines.
- **Tenant:** `inputs: { tags: [], direct_globs: "scripts/*.sh", find_dir: "" }`.

## ci/lint/docs-link-check.yml

- **Reproduces:** weisssrv `lint-docs-links`.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `lint-docs-links` | the name every consumer already passes |
| `stage` | `lint` |  |
| `image` | `python:3.11` | must ship git: the checker enumerates tracked Markdown and fails loud without it, so a slim image cannot silently shrink the scan |
| `tags` | `["infrastructure"]` |  |
| `script_path` | `scripts/check-doc-links.py` |  |
| `roots` | `""` | empty = the checker's own default scope |
| `default_branch` | `main` |  |
| `changes` | `["**/*.md", "scripts/check-doc-links.py"]` |  |

- **Parity — the script halves agree, and the `changes` default matches the
  scan.** `scripts/check-doc-links.py` here and weisssrv's repo-local copy both
  scan **every git-tracked `*.md` in the repo** (role, app and agent READMEs
  cross-link into `docs/` too), falling back to `docs/` plus
  `$CHECK_DOC_LINKS_EXTRA` only outside a git checkout. The `changes` default
  mirrors that scope, so no consumer needs to widen it. Neither `changes` entry
  matches `scripts/test_check_doc_links.py`, so an MR touching only the
  checker's test does not fire the job.
- Tenants vendor the stdlib-only checker from `scripts/check-doc-links.py` (no
  network, no dependencies). Re-vendor it at each tag bump — all three consumers
  now FAIL their own test suite on a drifted copy (see "A vendored script is a
  pin too" below), so a skipped re-vendor is loud rather than silent.
- **Tenant:** `inputs: { tags: [] }` (after vendoring the script).

## ci/lint/runbook-anchors.yml

- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `runbook-anchors` |  |
| `stage` | `lint` |  |
| `image` | `python:3.13-slim` | stdlib only, so no pip step |
| `tags` | `["infrastructure"]` |  |
| `script_path` | `scripts/check-runbook-anchors.py` | the consumer's vendored copy |
| `rules_dir` | `kubernetes/infrastructure/observability` | the whole tree is walked |
| `docs_dir` | `docs` |  |
| `base_placeholder` | `${cluster_runbook_base_url}/` | the Flux substitution prefix an in-repo `runbook_url` must carry |
| `default_branch` | `main` |  |
| `changes` | observability tree, `docs/**/*.md`, the checker | a doc rename breaks an anchor without touching a rule file |

- **The `changes` default includes the docs tree on purpose.** An anchor dies
  when a heading is renamed, and that edit touches no rule file. A consumer that
  narrows `changes` to the observability tree only learns about it at the next
  rule edit.
- **`rules_dir` is a tree, not a `rules/` subdirectory.** A Loki ruler's rule
  files sit beside its chart values, and they carry the same `runbook_url`
  annotations. Pointing this at a narrower path silently drops them.
- **Exit 2 on an empty scan.** A `rules_dir` with no `runbook_url` at all is an
  operator error, not a pass, so a mis-set path fails the job instead of
  reporting a clean tree.
- Tenants vendor the stdlib-only checker from
  `scripts/check-runbook-anchors.py`. Re-vendor it at each tag bump.
- **Tenant:** `inputs: { tags: [] }` (after vendoring the script).

## ci/lint/comment-length.yml

- **Reproduces:** this library's own `comment-length` job, which includes it.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `comment-length` |  |
| `stage` | `lint` |  |
| `image` | `python:3.13-slim` | needs pip only when `pyyaml_version` is set |
| `tags` | `["infrastructure"]` |  |
| `script_path` | `scripts/check-comment-length.py` | the consumer's vendored copy |
| `paths` | `"."` | space-separated; the whole tree by default |
| `config` | `""` | e.g. `--config .comment-length.yml` |
| `pyyaml_version` | `6.0.2` | empty skips the install; only a YAML `config` needs it |
| `default_branch` | `main` |  |
| `changes` | `["**/*"]` | every file, because the convention covers every file |

- The gate fails a comment block over three content lines, or eight when the
  block opens with `CRITICAL:`. Content lines exclude the markers, the block
  delimiters and blank lines, so a summary line plus two body lines passes.
- It excludes tool caches and scratch trees on its own (`.git`, `.venv`,
  `.terraform`, `.ansible`, `.ansible-home`, `__pycache__`, `node_modules`,
  `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `*.egg-info`, `.tmp`,
  `.worktrees`) and skips any suffix it has no comment syntax for, so
  `paths: "."` is the normal setting.
- Adopt it after a sweep, not before: a repo with a backlog of long comments
  reds every pipeline from the first run. `scripts/check-comment-length.py`
  run locally over the tree tells you the size of that sweep.
- **Known limitation:** the scanner has no notion of string literals or
  heredocs. A `/*` inside a string is not treated as a block opener (an
  unterminated candidate is discarded), but four or more consecutive `#` lines
  inside an HCL heredoc are still grouped into one run and can fail the limit.
  Move such a block out of the heredoc, or exclude that file. A `.py.jinja`
  source is parsed with its jinja tags neutralized, so its docstrings are
  checked; one that still does not parse is a warning, not a failure, unless
  `config` carries `--strict-jinja`.
- **Tenant:** `inputs: { tags: [] }` (after vendoring the script).

## ci/lint/python-lint.yml

- **Reproduces:** nothing — no consumer carries a local Python-lint job; this
  template is the only one. This library self-applies it; weisssrv includes it over `scripts/`
  with the shared profile vendored to its repo root as `ruff.toml`; the app
  template includes it over `scripts tests` with the same profile vendored to
  its root (no `config:` input, so ruff's discovery finds it — which is what
  makes the GitLab job, the `github` shape's step and `task python-lint` report
  identically); and the cluster template includes it.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `python-lint` |  |
| `stage` | `lint` |  |
| `image` | `python:3.11-slim` |  |
| `tags` | `["infrastructure"]` |  |
| `ruff_version` | `0.16.7` |  |
| `config` | `""` | empty = ruff's own discovery; pass the FULL argument, e.g. `--config lint/ruff.toml` |
| `targets` | `.` |  |
| `default_branch` | `main` |  |
| `format_check` | `false` | for a consumer that adopts `ruff format` |
| `changes` | `["**/*.py", "ruff.toml", "pyproject.toml", ".gitlab-ci.yml"]` |  |

- **Selection:** `E4,E7,E9,F,W,B` from the shared profile in
  [`lint/ruff.toml`](../lint/ruff.toml), which documents why. `format_check`
  exists for a consumer that adopts `ruff format`.
- **Tenant:** `inputs: { tags: [], targets: "src tests" }` (after vendoring
  `lint/ruff.toml`, or with a `ruff.toml` / `[tool.ruff]` of its own).

## ci/lint/ansible-lint.yml

- **Reproduces:** weisssrv's inline `ansible-lint` job, which now includes this
  template instead (`targets: ansible/`, `galaxy_requirements:
  ansible/requirements.yml` so the collection the playbooks address by FQCN is
  installed first). This library also self-applies it over the `weisssrv.infra`
  collection, and the cluster template includes it for the generated repo's own
  `ansible/` tree.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `ansible-lint` |  |
| `stage` | `lint` |  |
| `image` | `python:3.13-slim` |  |
| `tags` | `["infrastructure"]` |  |
| `ansible_lint_version` | `26.8.0` | keep in step with `docker/molecule-ci/requirements.txt` so lint and molecule agree |
| `pip_extra` | `black==26.3.1` | held below the broken 26.5.x mypyc wheels; routed through a job variable, so a `<`/`>`/`|` ceiling is safe |
| `config` | `""` | empty = ansible-lint's own discovery; pass the FULL argument, e.g. `-c .ansible-lint` |
| `targets` | `.` |  |
| `collections_path` | `.` | exported as `ANSIBLE_COLLECTIONS_PATH`, singular ONLY: ansible-compat hard-errors on the legacy plural spelling |
| `galaxy_requirements` | `""` | empty = no install; point it at the requirements.yml declaring the collections the linted roles' FQCN refs need |
| `default_branch` | `main` |  |
| `changes` | `["**/*.yml", "**/*.yaml", ".ansible-lint", ".ansible-lint-ignore", ".gitlab-ci.yml"]` |  |

- **Non-root safe:** `pip install --user` + absolute user-base path; caches go
  to `$CI_PROJECT_DIR/.ansible-home`.
- **`pip_extra` is routed through a job variable (`PIP_EXTRA`), not
  interpolated into the pip line.** `$[[ inputs.* ]]` is textual substitution
  into the YAML scalar, so a version-ceiling pin like `black<26.5.0` would
  render a literal `<` that the shell parses as an input redirection *before*
  any expansion. The result of a parameter expansion
  is word-split but never re-scanned for redirection operators, so the variable
  form takes `<`, `>` and `|` safely. `python-tests` routes `pip_packages` /
  `apt_packages` the same way; pass ceilings freely in either.
- **Tenant:** `inputs: { tags: [], targets: "ansible/" }` (with its own
  `.ansible-lint`, or an empty `config` for defaults).

## ci/lint/terraform-tflint.yml

- **Extracted from** the near-identical local `terraform-tflint` job each
  consumer carried. tflint catches what `fmt` and `validate` do not: deprecated
  syntax, unused declarations and provider-specific rules.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `terraform-tflint` |  |
| `stage` | `lint` |  |
| `image` | pinned `ghcr.io/terraform-linters/tflint` digest | the job overrides the entrypoint |
| `tags` | `["infrastructure"]` |  |
| `work_dir` | `terraform` | a directory this repo does not have FAILS the job |
| `config` | `""` | path relative to `work_dir`, passed as `--config=`; empty lets tflint discover `.tflint.hcl` |
| `tflint_args` | `--recursive --minimum-failure-severity=error` | word-split |
| `default_branch` | `main` |  |
| `changes` | `["terraform/**/*", ".tflint.hcl", ".gitlab-ci.yml"]` |  |

- **Accounted, like `yaml-lint` and `shellcheck`:** a `work_dir` with no `*.tf`
  anywhere under it FAILS the job ("no *.tf under work_dir"), because
  `tflint --recursive` over an empty tree exits 0.
- **`config` is routed through a job variable (`TFLINT_CONFIG`),** so an empty
  value stays an empty word instead of rendering a bare `--config=`.
- **Not self-applied.** This repo ships module shapes with no roots to lint
  recursively; `ci/validate/terraform.yml` covers them.

## ci/validate/flux-lint.yml

- **Reproduces:** weisssrv `flux-lint` (substitute mode) and the tenant/cluster
  `flux-lint` (simple mode).
- **Key input `substitute` (boolean, default true):**
  - `true` (weisssrv): extract postBuild vars from a cluster-versions ConfigMap
    (`flux_render_script`), iterate `cluster_dir` Kustomizations, envsubst each,
    kubeconform, run the unvalidated-kind tracker, build the cluster root, then
    run `extra_validation`. Needs a **root** runner (installs `gettext-base`).
  - `false` (tenant): `kustomize build <kustomize_path> | kubeconform …`, with
    an empty-render floor and a skip-count gate (`allowed_skips`). Non-root safe
    (stdlib tool download into a workspace `.bin`).
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `flux-lint` |  |
| `stage` | `lint` |  |
| `image` | `python:3.11-slim` | **must ship bash** (`set -o pipefail`, `${!var+x}`), and apt on the root path |
| `tags` | `["infrastructure"]` |  |
| `substitute` | `true` | `true` = the cluster path, `false` = the tenant path |
| `kubeconform_version` | `0.8.0` |  |
| `kubeconform_sha256` | the sha for `kubeconform_version` | moves with it |
| `kustomize_version` | `5.8.1` |  |
| `kustomize_sha256` | the sha for `kustomize_version` | moves with it |
| `helm_version` | `3.22.0` |  |
| `helm_sha256` | the sha for `helm_version` | moves with it |
| `flux_version` | `2.9.0` | substitute mode only — the flux CLI whose `envsubst --strict` decides whether the post-build will reconcile. Held equal to the versions ConfigMap's own `flux_version` by the job, which fails on a mismatch rather than linting with a parser the cluster does not run |
| `flux_sha256` | the sha for `flux_version` | moves with it |
| `pyyaml_version` | `6.0.2` | pins the inline `spec.path` parser |
| `k8s_version` | `""` | empty = derived from the ConfigMap's `k3s_version` (substitute mode); simple mode falls back to 1.36.0 |
| `kustomize_path` | `kubernetes/flux` | simple mode only — the ONE directory that arm builds. The default is the tenant layout the app template renders; a repo laid out differently passes the path it actually reconciles |
| `allowed_skips` | `"0"` | simple mode only. How many rendered resources kubeconform may validate against NO schema. Above it the job fails, and so does a summary carrying no `Skipped:` field, which is what an unreachable catalog looks like. Substitute mode uses `expected_skipped_file` instead |
| `cluster_dir` | `""` (required in substitute mode) | substitute mode only; the simple arm never reads it, so it need not be passed there. Empty in substitute mode FAILS the job — a cluster name is site data, so there is no real default |
| `require_cluster_root` | `true` | substitute mode; pass `false` pre-bootstrap |
| `versions_configmap` | `kubernetes/infrastructure/sources/versions-configmap.yaml` | substitute mode only |
| `flux_render_script` | `scripts/flux-render.sh` | substitute mode; the path is taken from the CONSUMER tree |
| `skipped_script` | `scripts/kubeconform-skipped.py` | substitute mode; consumer tree |
| `crd_catalog_ref` | a `datreeio/CRDs-catalog` commit sha | ref the kubeconform schema location resolves against. Pinned so a catalog rewrite cannot change what the gate accepts; point it at an internal mirror ref to self-host |
| `expected_skipped_file` | `""` | repo-local baseline of `apiVersion/Kind` pairs known to have no schema in the CRD catalog, one per line, `#` comments allowed. Empty keeps the unvalidated-kind tracker informational. Set it and a kind skipped outside the baseline FAILS the job — without it an unreachable or rate-limited catalog silently degrades the run to core-kinds-only while flux-lint reports green |
| `extra_validation` | `""` | shell run with `$RENDER_ALL` / `$FAILED` in scope; see `scripts/run-render-gates.sh` |
| `default_branch` | `main` |  |
| `changes` | `["kubernetes/**/*", "ansible/inventories/prod/group_vars/all.yml"]` |  |

- **Substitute-mode inputs:** `cluster_dir` (required in that mode),
  `versions_configmap`
  (`kubernetes/infrastructure/sources/versions-configmap.yaml`),
  `flux_render_script` (`scripts/flux-render.sh`), `skipped_script`
  (`scripts/kubeconform-skipped.py`), `require_cluster_root` (true),
  `extra_validation` (empty), `helm_version` / `helm_sha256` and
  `flux_version` / `flux_sha256`.
- **Simple-mode inputs:** `kustomize_path` (`kubernetes/flux`), `k8s_version`,
  `allowed_skips` (`"0"`). Every other input above belongs to substitute mode.
- **Neither arm can pass on nothing.** The simple arm fails when
  `kustomize build` renders no document (an emptied or mistyped `resources:`
  list, which both tools exit 0 on while the cluster-side Kustomization's prune
  deletes what the repo applied), and fails when kubeconform skipped more
  resources than `allowed_skips` or printed no `Skipped:` field at all.
  Substitute mode has the same two floors: the empty-`$RENDER_ALL` check and
  the unvalidated-kind tracker.
- **Flux's own envsubst is the authority on substitution (substitute mode).**
  Each rendered tree is piped through `flux envsubst --strict` before the GNU
  `envsubst` render, and the file fails on a non-zero exit with flux's message
  quoted. GNU envsubst reads only `${NAME}`, while Flux's Go implementation also
  reads bash modifiers — `${conf%/*}` is a variable to it — so a form only it
  sees would otherwise pass this gate and leave the Kustomization BuildFailed
  in-cluster, reconciling nothing. The cheap `${`-shape pre-scan stays ahead of
  it: that one names the offending line, which `--strict` does not. The tenant
  arm substitutes nothing, so it installs no flux CLI.
- **`k8s_version` has no silent fallback in substitute mode.** When it is empty,
  `flux-render.sh k8s-version` derives the schema version from the versions
  ConfigMap's `k3s_version` key — and **fails the job** if that key is absent or
  unparseable. It is read through a YAML parse, so a `k3s_version` outside
  `.data` does not match. A consumer whose ConfigMap lacks the key passes
  `k8s_version` explicitly. **Simple mode still defaults**: the tenant branch
  uses `${K8S_VERSION_INPUT:-1.36.0}`, so a tenant that omits `k8s_version`
  validates against 1.36.0 rather than failing. `scripts/validate-helm-values.py`
  keeps the same `1.36.0` fallback for the `extra_validation` hook — see its
  section in [SCRIPTS.md](SCRIPTS.md).
- **`export-versions` rejects reserved key names.** A ConfigMap key that would
  clobber the calling job's own shell variable (`PATH`, `HOME`, `CLUSTER_DIR`,
  `FAILED`, `RENDER_ALL`, `K8S_VER`, `VARS`, `FLUX_ENVSUBST_VARS`, or anything
  ending `_SHA256`) is a hard error rather than an `eval` that silently rewrites
  the job's environment. Generated keys are lowercase, so no current consumer is
  affected.
- **Cluster-root build (substitute mode) is bootstrap-aware, opt-out.** After
  the per-Kustomization loop the job builds `cluster_dir` itself, to catch a
  malformed top-level `kustomization.yaml`. That root pulls in `flux-system/`,
  whose `gotk-components.yaml` and `gotk-sync.yaml` are written by `flux
  bootstrap` — so before bootstrap they do not exist and the build cannot
  succeed. `require_cluster_root` therefore defaults to **true**: a bootstrapped
  consumer always builds its root, so losing that content fails instead of
  quietly skipping. **A pre-bootstrap consumer must pass
  `require_cluster_root: false`** — a freshly generated cluster repo is exactly
  that case — and even then the skip applies only while **both** gotk files are
  absent. If either exists, the root is built so a missing companion file fails
  loudly. A generated repo that omits the input fails its first pipeline; the
  job prints the input to pass.
- **Parity:** the render loop, missing-placeholder check, envsubst allowlist and
  informational skip tracker are extracted from weisssrv. **Full weisssrv parity
  requires passing `extra_validation`** with its HPA/VPA invariant,
  scrape/NetworkPolicy invariant, secret-store scoping, PVC storage-class and
  helm-values calls — those reference weisssrv-local scripts that stay in
  weisssrv and run with `$RENDER_ALL` / `$FAILED` in scope.
  `flux-render.sh` and `kubeconform-skipped.py` are shipped here AND vendored in
  weisssrv.
- **Tenant:** `inputs: { tags: [], substitute: false, kubeconform_version:
  "0.8.0", kubeconform_sha256: "…", kustomize_version: "5.8.1",
  kustomize_sha256: "…", k8s_version: "1.36.0" }` (its own newer pins).

## ci/validate/terraform.yml

- **Reproduces:** weisssrv `terraform-fmt` + `terraform-validate` — one include,
  **two** jobs.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `fmt_job_name` | `terraform-fmt` |  |
| `validate_job_name` | `terraform-validate` |  |
| `fmt_stage` | `lint` |  |
| `validate_stage` | `validate` | a pipeline with no validate stage passes `lint` |
| `image` | `hashicorp/terraform:1.15` |  |
| `tags` | `["infrastructure"]` |  |
| `fmt_dir` | `terraform/` |  |
| `module_glob` | `terraform/*/` |  |
| `test` | `false` | off so a consumer with no `*.tftest.hcl` keeps the pre-`test` behaviour |
| `default_branch` | `main` |  |
| `changes` | `["terraform/**/*"]` |  |

- **Parity:** defaults reproduce both jobs' script and rules verbatim.
  `default_branch` is applied to BOTH jobs' post-merge rule. `test` defaults
  **off** so a consumer with no `*.tftest.hcl` keeps the pre-`test` behavior.
- **`test: true` runs `terraform test` in every module that ships test files**
  (`tests/*.tftest.hcl` or `*.tftest.hcl`), on top of validate. This is the only
  way the module's variable `validation` blocks and resource `precondition`s
  run: `terraform validate` evaluates no caller values. Plan-only against a
  `mock_provider`, so it needs no credentials — but a test file WITHOUT a
  `mock_provider` would try to reach the real provider, so keep the mock. Like
  `module_glob`, it is accounted: `test: true` with no matching test file FAILS
  the job ("test: true but no module under module_glob ships a *.tftest.hcl").
- **Self-applied** over `terraform/modules/` (`module_glob:
  "terraform/modules/*/"`, `validate_stage: lint` — this pipeline has no
  validate stage, `test: true` — all four modules ship
  `tests/validation.tftest.hcl`).
- **A `module_glob` that matches no module with a `versions.tf` FAILS the job**
  ("module_glob matched no module with a versions.tf"), the same accounting
  `yaml-lint` and `shellcheck` apply. Individual dirs without a `versions.tf`
  are still skipped; point the glob at the level that actually holds the
  modules.

## ci/validate/terraform-drift-plan.yml

- **New capability.** One read-only `terraform plan -detailed-exitcode` per
  module, with `allow_failure: {exit_codes: [2]}` so drift is an advisory yellow
  while a broken detector still fails red.
- **Not self-applied.** This repo ships module shapes, not roots: no provider
  credentials and no Terraform state, so there is nothing here to plan against.
  First rendered in a consumer.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `terraform-drift-plan` |  |
| `stage` | `validate` |  |
| `image` | `hashicorp/terraform:1.15.9` |  |
| `tags` | `[]` | tag-less by default; it needs only terraform and the op CLI |
| `module_dir` | **required** | the root module to plan |
| `state_name` | **required** | the HTTP backend state this module owns |
| `state_base` | the project's terraform state API base | the three `TF_HTTP_*ADDRESS` values are derived from it |
| `secrets_exports` | `""` | shell exporting this module's provider credentials, run under `set -eo pipefail` in the module directory before the plan. Assign then export; `export X=$(op read ...)` masks a failed read and plans with an empty credential |
| `changes` | `["terraform/**/*"]` |  |
| `default_branch` | `main` |  |
| `secrets_guard` | `"true"` | the expression that must be non-empty for the job to be created. The default always creates it, so a missing credential reds the job. Falsy means no job, not a failed job, so pass `"$OP_SERVICE_ACCOUNT_TOKEN"` only where a fork-safe skip matters more than the detector. Drift itself surfaces as an allowed failure (exit code 2), which GitLab does not notify on: enable pipeline-failure notifications on the schedule, or set `allow_failure: false` on the job where drift should page |

- **Include it once per module**, each with its own `job_name`, `module_dir` and
  `state_name`. It requires `.terraform-http-backend` and
  `.install-1password-alpine` in the same pipeline.
- **No `merge_request_event` rule, deliberately.** The job reads provider
  credentials, and must not do that in a job running an unmerged branch's code.
- `default_branch` takes a literal branch name, like every other job-defining
  template.

## ci/validate/cluster-drift-plan.yml

- **New capability.** The live-cluster counterpart of
  `terraform-drift-plan`: it runs the consumer's policy gates through `kubectl`
  and reports, never reconciles.
- **The three-way exit contract is the point.** Each gate exits **0** clean,
  **1** drift, **2** uninspectable input — an expired kubeconfig, a revoked
  token, a renamed CRD. The job keeps the HIGHEST code across its gates and
  declares `allow_failure: {exit_codes: [1]}`, so drift is the advisory yellow
  while rc 2 stays RED. A blanket `allow_failure: true` would collapse rc 2 into
  the allowed case and the detector would stop detecting in silence.
- **Not self-applied.** This repo has no cluster to inspect.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `cluster-drift-plan` |  |
| `stage` | `validate` |  |
| `base` | `.cluster-verify-base` | the hidden job supplying kubectl and the kubeconfig; match its `fragment_name` |
| `gates` | **required** | shell run once per detector, each line `drift_gate '<one shell pipeline>'`. The helper runs the gate in a subshell, so a gate's own `exit` ends that gate only; a gate run outside `drift_gate` loses its exit code and the detector reads as clean |
| `secrets_guard` | `"$OP_SERVICE_ACCOUNT_TOKEN"` | expression that must be truthy for the job to be created. Interpolated inside a single-quoted `rules:if`, so it carries no single quote |

- **Write each gate so it owns its input.** Under `pipefail` a failed `kubectl`
  feeding a parser that exits 0 reports rc 1, which reads as drift. Capture the
  query first and exit 2 when the capture fails.
- **Schedule only, deliberately.** The job reads a cluster-admin kubeconfig from
  the vault, so it must not run on unmerged branch code, and its subject is the
  live cluster rather than the commit. Drift surfaces as an allowed failure,
  which GitLab does not notify on: enable pipeline-failure notifications on the
  schedule, or set `allow_failure: false` where drift should page.
- It requires `ci/deploy/cluster-verify-base.yml` in the same pipeline.

## ci/security/secret-detection.yml

- **Reproduces:** weisssrv `secret_detection`; the same job in both templates.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `stage` | `security` |  |
| `tags` | `["infrastructure"]` | set on the override job |
| `cpu_selector` | **required** | REQUIRED. A node label is site data, so the template ships no default |
| `historic_scan` | `false` | string, not boolean: it is forwarded to the managed template's own variable |
| `default_branch` | `main` |  |
| `allow_failure` | `false` | `false` is what makes the gate real |

- **It nests a GitLab-MANAGED template, and that is the one dependency in this
  family that moves without a `ref:` bump.** The file does
  `include: - template: Jobs/Secret-Detection.gitlab-ci.yml` and then overrides
  the `secret_detection` job. The nested template — and therefore the analyzer
  image and the rule set it runs — is resolved from the GitLab *instance*, so it
  changes on an instance upgrade even though the consumer's pin did not move.
  There is no supported way to pin it. Consequences: a scan result can change
  with no diff in any repo, and a GitLab upgrade is a legitimate suspect when
  this job starts failing or stops finding something. Everything else in the
  family — template refs, tool binaries, images, the collection — is pinned.
- **`allow_failure: false` is what makes the gate real.** An `allow_failure:
  true` job counts as SUCCESSFUL for a downstream `needs:`, so a gate job that
  needs this one does **not** block on findings. Pass `allow_failure: true` for
  deliberate advisory-only mode (findings still produce the security report and
  a red-flagged job; nothing blocks).
- **`cpu_selector` is REQUIRED.** A node label is site data, so neither this
  template nor `docker-build` ships a default. The job always emits
  `KUBERNETES_NODE_SELECTOR_CPU`, so the value must satisfy the runner's
  `node_selector_overwrite_allowed` regex; `""` fails that regex and the job
  errors at pod creation. A runner that sets no regex ignores the variable
  entirely. gitleaks additionally needs POPCNT/SSE4.2, so this job must land on
  a modern-CPU node.
- Pair with `lint/gitleaks.toml` + `lint/secret-detection-ruleset.toml` (vendored
  as `.gitleaks.toml` and `.gitlab/secret-detection-ruleset.toml`). The
  allowlist covers the two published supply-chain pins the CI templates carry
  (1Password's apt GPG fingerprint and apk key sha256), which otherwise trip
  gitleaks' entropy rule and — now that findings block — would fail the job.

## ci/build/docker-build.yml

- **Reproduces:** the DinD `build_and_push` mechanics from weisssrv's
  `.build-molecule-base` (static docker CLI sha-pinned, dind wait, registry layer
  cache + inline cache, bounded retry, `:<sha>` always + `:latest` on the default
  branch). weisssrv itself does NOT include it — only the app template does.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `build-image` |  |
| `stage` | `build` |  |
| `image` | `python:3.11` |  |
| `tags` | `["infrastructure"]` | **must be a privileged runner** |
| `dind_service` | `docker:27.5.1-dind`, digest-pinned | digest-pinned, with an explicit `alias: docker` |
| `dind_mtu` | `1420` | MTU of the daemon's bridges, passed as both `--mtu` and `--default-network-opt=bridge=com.docker.network.driver.mtu`; must not exceed the job pod's interface MTU (1420 on flannel over WireGuard or VXLAN). `--mtu` alone covers only the default bridge, so a user-defined network (molecule, compose) would stay at 1500 and black-hole large TLS frames |
| `docker_cli_version` | `27.5.1` |  |
| `docker_cli_sha256_amd64` | the sha for `docker_cli_version` | moves with it |
| `docker_cli_sha256_arm64` | the sha for `docker_cli_version` | moves with it |
| `buildx_version` | `v0.35.0` |  |
| `buildx_sha256_amd64` | the sha for `buildx_version` | moves with it |
| `buildx_sha256_arm64` | the sha for `buildx_version` | moves with it |
| `registry` | `$CI_REGISTRY_IMAGE` | must agree with the login trio |
| `login_registry` | `$CI_REGISTRY` |  |
| `login_user` | `$CI_REGISTRY_USER` |  |
| `login_password` | `$CI_REGISTRY_PASSWORD` |  |
| `image_name` | `""` | empty = push to the registry base |
| `context` | `.` |  |
| `dockerfile` | `""` | empty = the context's Dockerfile |
| `extra_build_args` | `""` |  |
| `default_branch` | `main` |  |
| `publish_on_main` | `true` | also gates the `:latest` publish |
| `changes` | `["**/*"]` | matches everything; narrow it to the build context |
| `schedule_when` | `on_success` | `never` opts out of the scheduled rebuild |
| `digest_dotenv_var` | `""` | empty = off; set a variable NAME to pin the pushed digest for a later job |
| `digest_dotenv_file` | `image-digest.env` |  |
| `cpu_selector` | **required** | REQUIRED. A node label is site data, so the template ships no default |

- **Schedules rebuild by default — this is the one template that does not
  exclude them.** `changes:` always evaluates true on a scheduled pipeline and
  the layer cache is deliberately skipped there, so a schedule is a
  fresh-dependency canary that also re-pushes `:latest` on the default branch.
  Pass `schedule_when: never` to opt out; `publish_on_main` is not the lever
  (it also disables `:latest` on ordinary merges). The library's own three image
  builds pass `never`: its only schedule is the full molecule matrix, which must
  not push images. Image freshness there is the weekly version-check / bump bot
  plus the digest pin gate, not the scheduled rebuild.
- **`dind_service` is digest-pinned and carries an explicit `alias: docker`.**
  The service is a map, not a bare string, because the runner derives the
  network alias from the image name and a digest-bearing name must not be left
  to that derivation — `DOCKER_HOST=tcp://docker:2375` has to resolve. Bumping
  this pin changes the daemon under every build and every molecule job that
  reuses it; treat it as its own change.
- **buildx:** the static docker CLI ships no buildx plugin, so with
  `DOCKER_BUILDKIT=1` the pinned plugin is what makes `docker build` work at
  all. Version + both per-arch sha256s are inputs on the same footing as the
  docker CLI trio (VERSIONING.md § Pinned tool versions inside templates);
  bump all three together, or override them per-consumer for a different
  buildx.
- **OCI provenance is applied by the job, not the Dockerfile.** Every build gets
  `org.opencontainers.image.{source,revision,version,title}` via `--label`
  (from `CI_PROJECT_URL` / `CI_COMMIT_SHA` / `CI_COMMIT_TAG` or the short sha /
  `image_name`), so an image pulled cross-project traces back to its commit with
  no consumer Dockerfile change. A `LABEL` of the same key in a Dockerfile is
  overridden — the job's value is the authoritative one.
- **The job declares no `needs:`, and must not grow one.** Consumers order the
  privileged build AFTER the secret scan by stage alone, so a `needs:` would
  release it from stage order and let it build a tree the scan has not seen.
  `tests/test_secret_detection_ci.py` holds that.
- **`default_branch` gates BOTH the post-merge rule and the `:latest` publish**,
  and must be a literal name (see the shared conventions above).
  `release_branch` in `ci/release/semantic-release.yml` is literal for the same
  reason.
- **`changes`:** the default `["**/*"]` matches everything. Narrow it to the
  image's build context (plus anything baked into it) instead of overriding
  `rules:` wholesale: an included job merges key-by-key with a local job of the
  same name, but `rules:` is REPLACED, so a copy silently forks from the
  template if its rules semantics ever change. This library's three image builds
  each narrow `changes` to their own build context, which is what keeps their
  rules disjoint; `build-molecule-ci` additionally sets
  `digest_dotenv_var: MOLECULE_CI_IMAGE` so the matrix and the child pipeline
  pin the exact image it pushed. The `web` clause stays manual and ungated
  regardless.
- **`digest_dotenv_var`:** set it to a variable NAME (e.g. `MOLECULE_CI_IMAGE`)
  and the job emits a dotenv report pinning that variable to the pushed image's
  immutable digest ref. A job that `needs:` this one with `artifacts: true` — or
  a `trigger:` bridge that forwards it into a child pipeline — then resolves the
  exact image this pipeline built, instead of a `:latest` anyone can retag
  mid-flight. Left empty the dotenv artifact is still produced but EMPTY, so it
  injects nothing (the report key is static; a missing file would warn on every
  run). Pin only the image a later job runs *as*: an image the job pulls by its
  immutable `:<short-sha>` tag is already pinned.
- **`cpu_selector` is REQUIRED**, for the reason above: a node label is site
  data. The job always emits `KUBERNETES_NODE_SELECTOR_CPU`, so the value must
  satisfy the runner's `node_selector_overwrite_allowed` regex, `""` fails that
  regex, and a runner that sets no regex ignores it entirely.
- **`registry` and the login are separate on purpose**, but they must agree:
  pointing `registry` at ghcr.io / Docker Hub / a Harbor host without also
  passing `login_registry` + `login_user` + `login_password` pushes
  unauthenticated and fails. The defaults render byte-identically to the
  hard-coded `$CI_REGISTRY*` trio they replaced.
- **`image` must be Debian-based.** The before_script derives the arch with
  `dpkg --print-architecture`, so an Alpine or UBI base fails there — the same
  class of image requirement `flux-lint` documents for bash.
- **The shared tenant runner is non-privileged and CANNOT build.** This template
  needs a privileged runner; a consumer without one cannot use it at all.
- **`:latest` is default-branch-only, deliberately.** An MR build never writes
  it — privileged CI jobs consume that tag, so unreviewed code must not be able
  to populate it. A repo whose default branch has never built the image
  therefore has no `:latest`: bootstrap it with one default-branch pipeline. MR
  pipelines do not need it — they resolve the image they just built through
  `digest_dotenv_var` or the immutable `:<short-sha>` tag.
- **Consumer (with own privileged runner):** `inputs: { tags: [their-runner],
  context: ".", dockerfile: "Dockerfile", cpu_selector: "<their pin>" }`.

## ci/test/python-tests.yml

- **Reproduces:** weisssrv `python-tests`.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `python-tests` |  |
| `stage` | `test` |  |
| `image` | `python:3.11-slim` |  |
| `tags` | `["infrastructure"]` |  |
| `test_dir` | `scripts/` |  |
| `pytest_version` | `9.1.1` |  |
| `pyyaml_version` | `6.0.2` |  |
| `apt_packages` | `git jq` | the one root-only default in the library; a tenant clears it |
| `pip_packages` | `""` | extra pinned pip specs; routed through a job variable, so ceilings are safe |
| `setup_command` | `true` | one command, run after the apt install and before the pip install; `python3 scripts/ci-fetch-tools.py jq amtool` drops verified static binaries into `$CI_PROJECT_DIR/.bin` |
| `tools_cache_key_files` | `["scripts/ci-fetch-tools.py"]` | consumer path(s) keying the `.bin/` cache; one or two entries, a missing one ignored |
| `default_branch` | `main` |  |
| `changes` | `["scripts/**/*", ".gitlab-ci.yml"]` |  |

- **Parity:** the junit report, the before_script (apt + pinned pip) and the
  rules are verbatim. The before_script puts `$CI_PROJECT_DIR/.bin` and the
  pip `--user` bin directory on PATH, so a CLI from `pip_packages`
  (`ansible-playbook`, `copier`) and a binary from `setup_command` resolve by
  name; a suite that fails rather than skips without its tool under `$CI`
  relies on both. The default `changes` is the generic subset only.
  **weisssrv's suite is mostly drift guards that read files outside
  `scripts/`**, so with the default list a guard could not fire on its own
  subject; weisssrv passes a ~28-entry `changes` covering the ansible,
  kubernetes, terraform, docs and Taskfile paths its tests read. Defaults are
  NOT byte-identical for this job. Derive such a list by tracing what the suite
  opens, not by reasoning about what it "should" read.
- **`.bin/` is cached** under the key prefix `python-tests-bin`, keyed on
  `tools_cache_key_files` (default the vendored `scripts/ci-fetch-tools.py`,
  so a pin bump busts it), `policy: pull-push`. A job whose `setup_command`
  fetches tools stops re-downloading them from GitHub once per pipeline — the
  same egress that produces the DinD-MTU reset class. The cache cannot serve a
  stale binary: `ci-fetch-tools.py` stamps `<name>.version` beside each one and
  re-fetches when the stamp does not match the resolved pin, so a version bump
  still takes effect even when the key does not change. Do not cache `.bin/`
  with a fetcher that lacks that stamp. No-op without a runner cache backend,
  and a no-op in substance for the default `setup_command: true`.
- **Tenant:** `inputs: { tags: [], apt_packages: "", image: python:3.13,
  test_dir: "tests" }`. `apt_packages` is the one root-only default in the
  library: installing them needs write access to the apt lock, which the shared
  non-privileged runner does not have, so a tenant clears it and picks an image
  that already ships what its tests need (the full `python:3.13` has git).

## ci/review/pr-agent.yml

- **Reproduces:** the `pr-agent-review` job in all three consumers. Runs
  `pragent/pr-agent:0.45.0` on gpt-5.6 / `high` with committable inline
  suggestions (`PR_CODE_SUGGESTIONS__DUAL_PUBLISHING_SCORE_THRESHOLD`,
  `CONFIG__PERSISTENT_INLINE_COMMENTS`); `allow_failure: true`, the schedule
  exclusion and the MR-only token-gated rule are baked in. weisssrv
  passes `secrets_source: env`, its own `gate` and a lint-only `needs` list.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `pr-agent-review` |  |
| `stage` | `ai-review` |  |
| `image` | `pragent/pr-agent:0.45.0`, digest-pinned | multi-arch index digest |
| `tags` | `["infrastructure"]` |  |
| `needs` | `[]` |  |
| `model` | `gpt-5.6` |  |
| `reasoning_effort` | `high` |  |
| `max_model_tokens` | `900000` |  |
| `ai_timeout` | `1200` |  |
| `dual_publishing_threshold` | `6` |  |
| `commands` | `review improve` |  |
| `extra_instructions` | a thoroughness prompt | drives BOTH `PR_REVIEWER__EXTRA_INSTRUCTIONS` and `PR_CODE_SUGGESTIONS__EXTRA_INSTRUCTIONS`, so it applies to whichever of `review` / `improve` `commands` runs |
| `gitlab_url` | `$CI_SERVER_URL` |  |
| `timeout` | `45m` |  |
| `secrets_source` | `env` | `env` reads the two CI variables; `1password` reads them with `op` at job time and needs a root runner plus the op CLI |
| `openai_key` | `$OPENAI__KEY` | a CI variable REFERENCE, env mode only |
| `gitlab_token` | `$GITLAB__PERSONAL_ACCESS_TOKEN` | a CI variable REFERENCE, env mode only |
| `op_openai_key_ref` | `""` | 1password mode; required there, consumer data |
| `op_gitlab_token_ref` | `""` | 1password mode; required there, consumer data |
| `gate` | `$OPENAI__KEY` | the raw `rules:if` expression, unquoted |

- **`gate` takes single quotes.** It lands in a `rules:if` expression; write it
  as the raw expression (`$OPENAI__KEY && $GITLAB__PERSONAL_ACCESS_TOKEN`), not
  pre-quoted.
- **Secrets:** `secrets_source: env` reads the two keys from CI/CD variables
  (works on the non-root shared runner). `secrets_source: 1password` reads them
  with `op` at job time and needs a root runner **plus** the op CLI, which the
  consumer adds — the template defines no `before_script`, so this is additive:

  ```yaml
  pr-agent-review:
    before_script:
      - !reference [.install-1password, before_script]
  ```

  `op_openai_key_ref` / `op_gitlab_token_ref` default to `""` — they name a
  vault and item titles, which are consumer data. The job fails fast in
  1password mode when either is empty; env-mode consumers never read them.

  **The two modes differ in blast radius, not just plumbing.** The job runs on
  `merge_request_event`, and a same-named override in the branch under review
  merges with the included job, so the branch controls the surrounding script.
  In `env` mode the exposure is capped at the two masked variables; in
  `1password` mode the `op` session is reachable from that branch-supplied
  config and reaches **every item the service account can see**. Scope the
  service account to a vault holding only these two items, or stay on `env` —
  which is why both this library and weisssrv do.
- **Token scope — never an admin PAT.** The GitLab credential (either path)
  should be a **project access token on the reviewed project with the
  `api` scope and the Developer role** — enough to post discussions, apply
  suggestions and label. The job runs on `merge_request_event` with config
  taken from the branch under review, and exports the token into a third-party
  image that is handed the diff, so an instance-admin or group-owner PAT turns
  any MR author into an admin-API caller. This applies to the `env` path too:
  the default `gitlab_token: "$GITLAB__PERSONAL_ACCESS_TOKEN"` inherits
  whatever the consumer put in that CI variable.
- **This library:** defaults (env mode, `$OPENAI__KEY` gate).
- **Tenant (BYO keys):** `inputs: { tags: [], commands: "review", timeout: "30m",
  openai_key: "$AI_REVIEW_OPENAI_KEY", gitlab_token: "$GITLAB_REVIEW_TOKEN",
  gate: "$AI_REVIEW_OPENAI_KEY && $GITLAB_REVIEW_TOKEN" }`.

## ci/release/semantic-release.yml

- **New capability** (no weisssrv job to reproduce): on a push to the release
  branch it reads the conventional commits since the last `<tag_prefix>X.Y.Z`
  tag, computes the bump, and creates the tag **and** the GitLab Release in one
  Releases API call. No releasable commit → no release, exit 0 (so re-running on
  an already-released commit is a no-op). Bump mapping and the notes format are
  in [VERSIONING.md](VERSIONING.md).
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `semantic-release` |  |
| `stage` | `release` |  |
| `image` | `python:3.13` | the full image ships git |
| `tags` | `["infrastructure"]` |  |
| `script_path` | `scripts/semantic-release.py` |  |
| `tag_prefix` | `v` |  |
| `initial_version` | `0.1.0` |  |
| `release_branch` | `main` | a **literal** branch name |
| `release_token` | `$CI_JOB_TOKEN` |  |
| `token_header` | `JOB-TOKEN` | `PRIVATE-TOKEN` with a PAT, when protected tags restrict `v*` |
| `major_on_zero` | `false` | a breaking change bumps MINOR while 0.x |
| `dry_run` | `false` |  |

- **`interruptible: false`.** A push to the release branch must not cancel an
  in-flight release job mid-API-call; that would tag without publishing notes,
  or publish twice on the retry.
- **Token:** `CI_JOB_TOKEN` suffices — the Releases API accepts it and creates
  the tag from `ref` (the Tags API itself is read-only for job tokens). Pass a
  PAT reference with `token_header: PRIVATE-TOKEN` if **protected tags** restrict
  who may create `v*`. See VERSIONING's protected-tag prerequisite.
- **This template is GitLab-only; the SCRIPT is not.** `semantic-release.py`
  takes `--platform {gitlab,github}` (default `gitlab`, so this template and
  every consumer of it are unaffected — it passes no such flag). In `github`
  mode the same vendored file targets
  `$GITHUB_API_URL/repos/:owner/:repo/releases` with `Authorization: Bearer`,
  reading `GITHUB_REPOSITORY` / `GITHUB_API_URL` / `GITHUB_SHA` /
  `GITHUB_TOKEN` where the GitLab path reads the `CI_*` set. Only the two API
  calls differ; the bump decision and the notes are forge-neutral, which is what
  keeps ONE byte-identical script in the library, the app template and the
  cluster template. Per-flag detail in [SCRIPTS.md](SCRIPTS.md#semantic-releasepy).
- **GitHub consumers** (the app template's `ci_shape: github`) have no `include:` to
  point at — the library ships no reusable Actions workflows — so they vendor
  [`ci/release/github-release-workflow.example.yml`](../ci/release/github-release-workflow.example.yml)
  as `.github/workflows/release.yml` next to the vendored script. It reproduces
  this job's contract in Actions terms: release branch only and never on a
  schedule, `workflow_run` on the CI workflow succeeding (Actions has no stage
  ordering to gate on), `concurrency` for `resource_group`, `fetch-depth: 0`
  for `GIT_DEPTH: 0`, and `release.json` uploaded `if: always()`. That file is a
  reference copy, NOT a template: nothing `include:`s it and re-vendoring is a
  manual step, but a consumer that copies it lists it in its
  `scripts/vendored-manifest.yml`, so `scripts/check-vendored-copies.py` fails
  that consumer on drift. Editing it is a coordinated two-repo change.
- **Requires:** the script vendored at `script_path`, `release` declared as the
  LAST stage (the job sets no `needs:`, so stage ordering gates it on the rest of
  the pipeline passing), and a `resource_group` — already set — to serialize
  rapid merges. Artifact: `release.json` — the OUTCOME, not the plan
  (`released` is true only after the API call succeeded; `dry_run` and `error`
  fields mark the other endings). Publish it `when: always`.
- **Self-applied:** this library wires it into its own `.gitlab-ci.yml`
  (`release` stage, `tags: []`), so the tag every consumer pins is cut by the
  merge that earns it and the template is exercised by the MR that changes it.

## ci/maintenance/version-check.yml

- **New capability.** The read-only half of the version pair: reports available
  updates and publishes a report artifact, changing nothing. `version-bump-bot`
  is the other half — it rewrites pins and raises the MR. A repo wants BOTH:
  this one so an MR author sees drift while they are already looking, the bot so
  drift is acted on when nobody is.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `version-check` |  |
| `stage` | `lint` | `lint`, so it runs inside a normal MR pipeline |
| `image` | `python:3.11` |  |
| `tags` | `["infrastructure"]` |  |
| `setup_command` | `true` |  |
| `check_command` | **required** | no default: the tools a checker needs are a property of that checker |
| `report_path` | `version-report.json` |  |
| `github_token` | `""` | a variable REFERENCE, exported before the consumer commands |
| `default_branch` | `main` |  |
| `soft_fail_exit_codes` | `[1]` | exit codes that do not redden the scheduled sweep or the post-merge run. `[1]` is "updates available"; a checker error (rc 2: revoked token, moved endpoint) reds the job. `[1, 2]` soft-fails both, and a transient upstream 5xx with them |
| `changes` | `["**/*"]` | NOT `[]`, which matches nothing and would delete the job silently |

- **Stage split with the bot is deliberate:** this job defaults to `lint` so it
  runs inside a normal MR pipeline, while `version-bump-bot` defaults to
  `maintenance` because it only ever runs on a schedule or a manual web trigger.
  A consumer that puts them in the same stage gets a bot job created on every
  merge request.
- **Retry parity:** this template carries the same
  `runner_system_failure`/`scheduler_failure` retry as every other job-defining
  template.
- **The template installs nothing, deliberately.** `setup_command` defaults to a
  no-op because the tools a checker needs are a property of that checker, which
  the library cannot see — the same reason `check_command` has no default. A
  guessed default would have to float or rot, and floating is the worse failure
  here: soft-fail means a checker that stops importing after an upstream release
  produces no report, and "no report" reads as "no updates". Install what your
  checker needs, pinned, in your own `setup_command`.
- **Soft-fail on every trigger, deliberately.** Most checkers signal "updates
  found" with rc=1, which is information rather than a defect — a scheduled or
  MR pipeline must not go red because upstream shipped a release.
- **`when: always` publishes a report that exists; it does not create one.** It
  means a non-zero exit will not suppress a report the check already wrote — not
  that a report appears regardless. A `check_command` that dies before writing
  `report_path` leaves nothing to upload, and the job goes green-ish (soft-fail)
  with no artifact at all. Write the report as early as the data allows, and
  treat "no artifact" as a check that failed before reporting rather than as a
  clean run.
- **`when: manual` carries its own `allow_failure: true`.** A rules-based manual
  job defaults to `allow_failure: false`, which leaves every web pipeline sitting
  "blocked" on a job nobody intended to play.
- **No credential retrieval in this job, by design.** `setup_command` and
  `check_command` both come from the `.gitlab-ci.yml` of the ref being tested, so
  on a merge request they *are* the code under review, sharing one shell. Any
  credential this job fetched would be readable by that code, and any guard
  around the fetch is defeatable by the step that runs first — an earlier
  `setup_command` can export `CI_COMMIT_REF_PROTECTED=true`, or eval the fetch
  command itself. A gate the attacker controls is not a gate. A consumer needing
  a token passes it as a masked CI variable, scoped to what it would tolerate
  leaking: an MR comment needs only Reporter + `api`, which can comment and read
  but not push. Vault-wide or user tokens do not belong here.
- **Credential handling and report creation are the CHECKER's job**, not this
  template's. The template runs `check_command` and uploads `report_path`; it
  does not fetch, validate or fall back on anything.

## ci/maintenance/version-bump-bot.yml

- **New capability.** A scheduled job that runs the consumer's own version-check
  command and keeps exactly ONE bot MR in sync: bumps present → force-push
  `branch` and create or refresh the MR; bumps unchanged from the branch's
  current content → nothing at all (no MR churn on a weekly schedule); no bumps
  with an MR open → close it. It **never merges**.
- **Inputs** — the three resource inputs every job template takes are in
  [Conventions shared by every template](#conventions-shared-by-every-template).

| Input | Default | Notes |
|---|---|---|
| `job_name` | `version-bump-bot` |  |
| `stage` | `maintenance` | `maintenance`: it only runs on a schedule or a manual web trigger |
| `image` | `python:3.13` |  |
| `tags` | `["infrastructure"]` |  |
| `script_path` | `scripts/version-bump-mr.py` |  |
| `setup_command` | `true` |  |
| `check_command` | **required** | the command that rewrites the pins; exits 0, or a code named in `check_soft_fail_exit_codes` |
| `check_soft_fail_exit_codes` | `1` | space-separated exit codes from `check_command` that mean "updates found" and let the bot continue; every other non-zero status fails the job, so a broken checker cannot look like an empty run. The codes apply to the whole command, so a composite `check_command` must keep its own error codes off this list |
| `paths` | `.` |  |
| `branch` | `bot/version-bumps` |  |
| `target_branch` | `main` |  |
| `title` | `chore(deps): version bumps` |  |
| `commit_message` | `chore(deps): update pinned versions` |  |
| `labels` | `""` |  |
| `report_path` | `""` | embedded in the MR description |
| `artifact_paths` | `[]` | paths published as job artifacts on every outcome; empty publishes nothing. The MR description embeds only the first 4000 characters of `report_path` and links to the artifact for the rest |
| `artifact_expire_in` | `30 days` | retention for `artifact_paths` |
| `git_user_name` | `version-bump-bot` |  |
| `git_user_email` | `version-bump-bot@noreply.invalid` |  |
| `bot_token` | `$VERSION_BUMP_BOT_TOKEN` | a PAT with `api` + `write_repository`; mask AND protect it |
| `run_branch` | `main` | the only ref the job may run on |
| `gate` | `$VERSION_BUMP_BOT_TOKEN` | must be non-empty for the job to be created |

- **`resource_group` + `interruptible: false`.** A schedule and a manual web run
  can no longer race each other on the same force-pushed branch and MR.
- **Token:** a PAT with `api` + `write_repository` — `CI_JOB_TOKEN` cannot push
  and cannot write the Merge requests API. It must be masked AND protected:
  protection is what keeps it out of scope on an unprotected ref, masking only
  hides it in logs.
- **Rules:** schedules and a manual web trigger, both restricted to `run_branch`
  and to `gate` being non-empty. `check_command` is the triggering ref's own
  shell running beside the PAT, which is what those two restrictions exist for;
  `tests/test_version_bump_bot_ci.py` asserts both on every rule. Untracked
  files are ignored, so a check command that drops a report artifact does not
  pollute the commit — point `report_path` at it instead.
- **`check_command` signals "updates found" by exit code.** Exit 0, or any code
  listed in `check_soft_fail_exit_codes` (default `1`), lets the bot continue;
  every other non-zero status fails the job. Do not wrap it in `|| true`: that
  hides a broken checker as an empty run, and the MR manager then closes the
  standing bot MR.
- **Not self-applied.** `check_command` has no generic value — it is the
  consumer's own version-check run against the consumer's own tracked-version
  config — and this library tracks no upstream versions, so its pipeline does
  not include this template. Alongside `flux-lint` and the `ci/templates/`
  fragments it is first rendered in a consumer; the consumer also owns the
  pipeline schedule that triggers it.

---

## Shared fragments (ci/templates/)

These define hidden jobs; `include` the file, then `extends` or `!reference` the
hidden job. They set no `retry` — the consumer's own job supplies it.
`ci/templates/install-1password.yml` takes a single input, `min_op_version`
(default `""`); the others carry `spec:inputs` whose defaults render
byte-identically to what they replaced.

- **dep-cache.yml** → `.dep-cache` (pip + galaxy cache). `extends: .dep-cache`.
  Inputs: `key_files` (`requirements.txt`, `ansible/requirements.yml`) and
  `cache_paths` (`.cache/pip`, `.cache/ansible-collections`). **`key_files` are
  consumer paths** — a repo that pins elsewhere must pass its own, or the key is
  computed from files that never change and "a dependency bump busts it" is not
  true for it. A listed path that does not exist is ignored silently, and
  **`key_files` takes at most two paths** (GitLab's `cache:key:files` limit — a
  third fails pipeline creation, not the job). A repo with three pin files keys
  on one concatenated lockfile instead. `cache_paths` has no such cap.
- **install-1password.yml** → `.install-1password` / `.install-1password-alpine`.
  `before_script: - !reference [.install-1password, before_script]`. Root runner
  only. Both fragments carry the hardened key verification: the apt fragment
  requires the downloaded material's set of PRIMARY (`pub`) fingerprints to be
  exactly the one expected key (a first-match check would let a bundle smuggle a
  second primary key past `gpg --dearmor`), and the apk fragment verifies in a
  tempfile and only then `install`s into `/etc/apk/keys`. The package comes from
  1Password's `stable` apt/apk suite and cannot be version-pinned without the
  pin breaking once upstream prunes that version, so a consumer that depends on
  a feature sets `min_op_version` and the fragment asserts a floor after
  install.
- **terraform-http-backend.yml** → `.terraform-http-backend` (GitLab HTTP state).
  `extends: .terraform-http-backend`. Inputs: `api_url` (`${CI_API_V4_URL}`) and
  `state_name` (`cloudflare`). `api_url` defaults to `${CI_API_V4_URL}` because
  `TF_HTTP_PASSWORD` is `${CI_JOB_TOKEN}`, valid only against the instance that
  issued it, so the address must resolve per-instance. `state_name` sets the
  pipeline's ONE default state (the hidden job's name is fixed, so a second
  include would collide); a job managing a DIFFERENT state overrides the three
  addresses per-job rather than including the fragment twice:

  ```yaml
  variables:
    TF_HTTP_ADDRESS: "https://.../terraform/state/<name>"
    TF_HTTP_LOCK_ADDRESS: "https://.../terraform/state/<name>/lock"
    TF_HTTP_UNLOCK_ADDRESS: "https://.../terraform/state/<name>/lock"
  ```

- **docker-dind.yml** → `.docker-dind`, the bootstrap half of an image-building
  job: the digest-pinned DinD service with its explicit `docker` alias, the
  `DOCKER_*` variables, the sha256-pinned static docker CLI and buildx plugin
  installs, the daemon readiness loop and the registry login. Every default is
  the value `ci/build/docker-build.yml` carries, and `tests/test_pin_parity.py`
  holds them equal. Not self-applied: this pipeline's image builds include
  `ci/build/docker-build.yml`, which carries the same body. Its `dind_mtu` is
  applied as both `--mtu` and `--default-network-opt`, so the networks molecule
  and compose create inherit it too. Inputs:
  `dind_service`, `dind_mtu`, `docker_cli_version`,
  `docker_cli_sha256_amd64`, `docker_cli_sha256_arm64`, `buildx_version`,
  `buildx_sha256_amd64`, `buildx_sha256_arm64`, `login_registry`,
  `login_user`, `login_password`, `service_memory_limit`,
  `service_memory_request`. Needs a
  PRIVILEGED runner. **`extends:` REPLACES `before_script`**, so a job with its
  own bootstrap step must start it with
  `- !reference [.docker-dind, before_script]`.

## Deploy templates (ci/deploy/)

The Ansible-deploy toolchain, extracted from the two cluster pipelines that had
copied it. **Root runner only** — every file here apt-installs or writes
`/usr/local/bin`.

**One secrets seam.** `secrets_source: 1password` is the default and unchanged:
`op read` for the SSH key and the kubeconfig, `op run --` around the playbook.
`secrets_source: env` takes the SSH key from `$SSH_PRIVATE_KEY` and the
kubeconfig from `$KUBECONFIG_B64` (masked CI variables), and
`secret_runner: ""` on `ansible-deploy` runs `ansible-playbook` directly. Two
couplings remain in `env` mode and are the consumer's to handle:
`LOKI_PUSH_USER` / `LOKI_PUSH_PASSWORD` are `op://` strings only `op run`
resolves, so override them on the job, and the base still extends
`.install-1password`. What is NOT here is the per-job matrix: `changes:` lists,
`op://` variable maps, resource groups and environment names are per-cluster by
definition, and parameterising them would trade duplication for indirection.

- **deploy-base.yml** → a hidden job (`fragment_name`, default `.deploy-base`)
  carrying `interruptible: false`, the `runner_system_failure` retry, the op
  CLI + dep cache (it `extends: [.install-1password, .dep-cache]`, so both
  `ci/templates/` fragments must be included too), the pinned ansible install,
  a 0600 `ansible.cfg` copy on a private path, the SSH key read under
  `umask 077`, the TOFU keyscan over `ALL_SSH_IPS`, and the collection install.

| Input | Default | Notes |
|---|---|---|
| `fragment_name` | `.deploy-base` |  |
| `image` | `python:3.13-slim` | must ship pip: the fragment pip-installs ansible |
| `tags` | `["infrastructure"]` |  |
| `secrets_source` | `1password` | `1password` reads the SSH key with `op read`; `env` takes it from `$SSH_PRIVATE_KEY` |
| `op_vault` | **required** | no default: a vault name is site data |
| `ansible_version` | `14.4.0` |  |
| `apt_packages` | `git` | needed when the collection installs from a `git+` URL; reaches the shell as `APT_PACKAGES` |
| `ansible_dir` | `ansible` |  |
| `hosts_env` | `scripts/hosts.env` |  |
| `ssh_key_item` | `SSH Key` |  |
| `ssh_key_field` | `private key` |  |
| `loki_item` | `Loki Push Auth` | set on the BASE, not per job |

  `apt_packages` reaches the shell as a job variable, not as interpolated text,
  so a version-ceiling pin is safe. A job extending the fragment inherits
  `APT_PACKAGES` and must not redefine it for another purpose.

  **`LOKI_PUSH_USER` / `LOKI_PUSH_PASSWORD` are set on the BASE**, not per job:
  a log-shipping role that renders `basic_auth` from empty values clobbers the
  fleet's config, and any job running `site.yml --limit` can reach one. The
  cost is that the Loki item must exist in `op_vault` — `op run` resolves every
  `op://` reference in the environment and a missing item is a hard failure.
  The op CLI, ansible and `apt_packages` installs this fragment performs are
  also available pre-baked, in the published `ansible-deploy` image
  ([`docker/README.md`](../docker/README.md)). The fragment is unchanged by it
  and still installs unconditionally, so a consumer that points the `image`
  input at it also drops the corresponding `before_script` steps to collect the
  saving.
- **kubectl-setup.yml** → a hidden job (default `.kubectl-setup`) whose
  `before_script` installs jq and a sha256-verified kubectl, then writes the
  kubeconfig from 1Password under `umask 077`. jq comes with the fragment
  because everything that reads `kubectl -o json` needs it and nothing else in
  the chain installs it; the step is skipped when the image already ships jq and
  uses apt or apk, so a consumer can drop its own install. Pull the fragment in
  AFTER the base's own `before_script`, both by `!reference` — a
  `before_script:` key defined on the extending job replaces the extended one
  rather than appending to it:

  ```yaml
  .maintenance-base:
    extends: .deploy-base
    before_script:
      - !reference [.deploy-base, before_script]
      - !reference [.kubectl-setup, before_script]
  ```

| Input | Default | Notes |
|---|---|---|
| `fragment_name` | `.kubectl-setup` |  |
| `kubectl_version` | `v1.35.2` |  |
| `kubectl_sha256` | the sha for `kubectl_version` | moves with it |
| `secrets_source` | `1password` | `1password` reads the kubeconfig with `op read`; `env` takes it from `$KUBECONFIG_B64` |
| `op_vault` | **required** | no default: a vault name is site data |
| `kubeconfig_item` | `K3s Kubeconfig` |  |
| `kubeconfig_field` | `kubeconfig` | the value must be base64 so the YAML survives the round trip |

- **cluster-verify-base.yml** → a hidden job (default `.cluster-verify-base`)
  for the in-cluster half of verification: `extends: .install-1password`, the op
  CLI and `kubectl-setup` `before_script`s by reference, then
  `kubectl version --request-timeout=5s` as a reachability probe. It carries
  `interruptible: false` and the `runner_system_failure` retry, and takes no
  Ansible, SSH key or `hosts.env`, so a kubeconfig-only check does not fail on a
  host it never talks to. Both referenced fragments must be included too, at
  their DEFAULT fragment names: the references here are literal, so a renamed
  `.kubectl-setup` fails pipeline creation. The probe is deliberately
  `kubectl version` and not `kubectl cluster-info`, which needs list permission
  on `kube-system` services a least-privilege runner ServiceAccount lacks.

| Input | Default | Notes |
|---|---|---|
| `fragment_name` | `.cluster-verify-base` |  |
| `tags` | `["infrastructure"]` | root-capable runner; no LAN or SSH reach needed |
| `image` | `python:3.13-slim` | Debian-based: the 1Password install is apt-based |

- **ansible-deploy.yml** → one deploy job: `op run -- ansible-playbook` from
  `ansible_dir`, `extends` the base, on `branch` (main) with the `gate`
  expression and a `changes:` list. Inputs `job_name`, `needs`,
  `resource_group`, `environment_name` and `changes` are **required** — each is
  per-job by nature, and defaulting them would silently collapse two jobs into
  one resource group or fire a deploy on the wrong paths. **`needs` is required
  for a stronger reason**: the only value the library could default it to is
  `[]`, and in GitLab `needs: []` is not "no dependencies declared" — it starts
  the job at pipeline creation, ahead of every lint and validate stage. An
  omitted gate would therefore be an *ungated* deploy against live
  infrastructure, so the template makes the consumer name the gate
  (`needs: [{job: validation-gate}]` is the shape both cluster pipelines use).

| Input | Default | Notes |
|---|---|---|
| `job_name` | **required** | per-job by nature |
| `stage` | `deploy` |  |
| `base` | `.deploy-base` |  |
| `needs` | **required** | required: `needs: []` starts the job at pipeline creation, ahead of every gate |
| `resource_group` | **required** | required: a shared default would collapse two deploys into one |
| `environment_name` | **required** | required |
| `ansible_dir` | `ansible` |  |
| `inventory` | `inventories/prod` |  |
| `playbook` | `playbooks/site.yml` |  |
| `extra_args` | `""` |  |
| `branch` | `main` |  |
| `secret_runner` | `op run --` | `""` runs `ansible-playbook` directly, for a consumer not on 1Password |
| `gate` | `$OP_SERVICE_ACCOUNT_TOKEN` | the expression that must be non-empty for the job to be created |
| `changes` | **required** | required: a default would fire a deploy on the wrong paths |

  The per-job **secret map is not an input** (`spec:inputs` has no map type,
  and `op run` resolves every `op://` reference in the environment, so a job
  must declare exactly what its playbook needs). Supply it by re-declaring the
  job name at top level, which merges at the map level with the generated one:

  ```yaml
  deploy-ansible-dns:
    variables:
      ADGUARD_ADMIN_PASSWORD: "op://<vault>/AdGuard Home/password"
  ```

  Use the same mechanism for a per-job `timeout`, extra `needs`, or a `script:`
  that runs two playbooks in sequence.

## GitHub workflow examples (ci/github/, ci/release/)

Vendorable references for a consumer on GitHub Actions, which cannot `include:`
anything from a private library. Copy them byte-identically into
`.github/workflows/` and re-vendor on a library bump; each carries a
canonical-copy header naming its source.

- **ci/release/github-release-workflow.example.yml** → `release.yml`. The
  Actions counterpart of `ci/release/semantic-release.yml`, running the same
  vendored `scripts/semantic-release.py` with `--platform github`.
- **ci/github/ci.example.yml** → `ci.yml`. The gate set, job by job:
  `yaml-lint`, `flux-lint` (`kustomize build` + kubeconform, carrying the same
  empty-render and non-zero-Skipped guards as `ci/validate/flux-lint.yml`'s
  simple mode), `manifest-gates` (`check-netpol-except-parity.py`,
  `check-scrape-wiring.py` — with `--namespace-from-tree`, because a
  byte-identical file cannot carry a tenant's namespace and a monitor scoped
  with `matchNames` is refused without one — and `check-kustomization.py`, each
  skipped with a `::warning::` when the repo does not ship it, and the job fails
  when NONE ran), `shellcheck`, `python-lint`, `comment-length`,
  `lint-docs-links`, `secret-detection` and a discarded `docker-build`. The job
  set is held to that list by `tests/test_github_example_gates.py`, and the tool
  pins are the same values as the library's template defaults, held by
  `tests/test_pin_parity.py`, so both CI shapes gate on identical tools.
  **Where parity stops:** the library-pin check (`check-lib-pins.py`,
  `check-molecule-image-pin.py`) has no job here — a GitHub consumer has no
  `include:` to drift and runs it from `task lint`.
  A consumer's shape-parity table should cite this list rather than re-derive it
  from the file, and a new step added here is release-noted as closing a parity
  gap.
- **ci/github/build-image.example.yml** → `build-image.yml`. Image build and
  push to GHCR, push-only and with no `workflow_dispatch` (it holds
  `packages: write`). A consumer that does not build an image should not vendor
  it at all — the app template's registry path is gated on `enable_image_build`
  for that reason, mirroring the GitLab shape, which gates its own build job on
  the same answer. Both workflows announce their no-Dockerfile skip with a
  `::warning::` and a step-summary line: a byte-identical file cannot tell
  "runs an upstream image" from "the Dockerfile was renamed", and a green job
  with every step skipped is invisible in the run list.

The copy relationship is recorded in each consumer's
`scripts/vendored-manifest.yml` and checked by
`scripts/check-vendored-copies.py` — the older per-consumer gates iterate
`scripts/` only and cannot see `.github/`. An example a consumer copies
without a manifest entry is a manual walk at bump time; the offer list
(`scripts/vendorable-paths.yml`) is what marks these files as vendorable.

---

## Terraform modules (terraform/modules/)

Not CI includes, but pinned the same way — a release tag, never a branch:

```hcl
module "zone" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/cloudflare-zone?ref=<CURRENT_TAG>"

  account_id = var.cloudflare_account_id
  zone_name  = var.external_domain
  records    = { ... }   # site data
}
```

A module's `required_version` floor is set by its shipped
`tests/validation.tftest.hcl`, not by its configuration, and it binds every
consumer — see
[`terraform/modules/README.md`](../terraform/modules/README.md).

Each module is a **shape**: resources, defaults and guardrails live here; the
inventory (records, ACL policy, SSO objects) is site data the caller passes in.
Modules declare `required_providers` only — the root module owns the `provider`
block, the backend (`ci/templates/terraform-http-backend.yml`), and the
lockfile.

| Module | Manages | Required inputs |
|---|---|---|
| `cloudflare-zone` | zone settings + DNS records with per-record destroy/drift protection | `account_id`, `zone_name` |
| `tailscale-acl` | tailnet ACL policy + Split-DNS nameservers | `acl_policy`, `split_dns` |
| `authentik-sso` | OAuth2/proxy/SAML providers, applications, groups, policy bindings, custom scope mappings, embedded outpost | none (every map defaults to `{}`) |
| `unifi-network` | UniFi networks/VLANs, custom firewall zones + zone-based policies, WLANs, client reservations, port forwards, hardened site settings | `networks` |

Behaviour to know before adopting:

- **`tailscale-acl.split_dns` has no default.** An unset value is a hard error,
  never a silently-planned destroy of live Split-DNS — pass `{}` for a tailnet
  that manages none.
- **`cloudflare-zone` routes each record to one of four resources** by its
  `protected` / `content_managed_externally` flags, because `lifecycle` blocks
  cannot take variables. Flipping a flag changes the resource address and needs
  a `moved {}` block.
- **`authentik-sso` defaults `oauth2_grant_types` to
  `["authorization_code","refresh_token"]`** (no ROPC/implicit/hybrid/
  client_credentials), defaults `matching_mode` to `strict`, and **rejects**
  regex redirect URIs containing an unescaped dot.
- **`authentik-sso` fails the plan on an application no ENABLED `policy_bindings`
  entry names** (it would be reachable by every authenticated user); a binding
  with `enabled = false` does not count, because the policy engine never
  evaluates it, and `allow_unbound` declares an open tile deliberate. Every object except policy bindings carries
  unconditional `prevent_destroy` — no per-object flag, because a flag would
  route the object to a different resource address, and an address change here
  is the destroy+create the flag exists to prevent. Removal is
  `terraform state rm` plus the map entry; renames use `moved {}`.
- **`unifi-network` takes `subnet` in GATEWAY form** (`10.0.30.1/24`, the host
  part IS the gateway) — the provider's own syntax, and a network-address
  `.0/24` fails the validation rather than planning a gateway the controller
  will not accept. `unifi_network` and `unifi_firewall_zone` carry unconditional
  `prevent_destroy`: destroying a network drops every client on that VLAN, and
  destroying a zone silently returns its networks to the default zone —
  segmentation gone, everything still routing.
- **`unifi-network` validates address CONTAINMENT, not only shape.** A DHCP pool
  outside its own network's subnet or covering that network's gateway, two
  overlapping `networks` subnets, a `clients[*].fixed_ip` outside its network or
  inside its DHCP pool, and a subnet overlapping a `reserved_cidrs` entry each
  fail the plan. A renumber is therefore one edit across the subnet, its pool and
  its reservations.
- **`unifi-network` manages objects, never devices.** Policy ORDER, mDNS
  reflection, per-port VLANs and 6 GHz are provider gaps at `~> 0.55.0`, not
  drift the module reports; the zone-per-network model is what makes unordered
  policies safe (allowances against a default inter-zone deny). Its README's
  "What this module cannot manage" table is the list, and the apply is
  supervised — this is the gateway's own segmentation.

Full input/output tables and the per-module consumption pattern are in each
module's `README.md`. `ci/validate/terraform.yml` covers them with
`fmt_dir: "terraform/"` and `module_glob: "terraform/modules/*/"`.

## Ansible collection (ansible_collections/weisssrv/infra)

Also not a CI include, also pinned by tag. Install it from git with the
collection's subdirectory appended to the repo URL:

```yaml
# ansible/requirements.yml
collections:
  - name: git+https://git.ericsweiss.com/eric/weisssrv-lib.git#/ansible_collections/weisssrv/infra
    type: git
    version: <CURRENT_TAG>   # a release TAG; a branch works for local iteration
```

```bash
ansible-galaxy collection install -r ansible/requirements.yml
```

Playbooks then address the roles by FQCN, which is what makes an upgrade
reviewable — nothing resolves off a local `roles/` path:

```yaml
- hosts: nas
  roles:
    - role: weisssrv.infra.nas_storage
    - role: weisssrv.infra.zfs_encryption
```

**Iterating on an unmerged collection change:** point Ansible at a checkout
instead of the installed copy — this repo already uses the
`ansible_collections/<ns>/<name>` layout, so the repo root *is* a valid
collections path:

```bash
ANSIBLE_COLLECTIONS_PATH=~/src/weisssrv-lib ansible-playbook site.yml
```

The role table and the inventory-wide alias table are in the
[collection README](../ansible_collections/weisssrv/infra/README.md); per-role
variables are in each role's own README. Site-specific
values (domains, IPs, pool names) are **inputs**, never role defaults — that is
the line between this collection and a cluster instantiation.

Two role-level contracts worth knowing before writing a play against them:

- A role that must reach another host (cert distribution, cluster-wide
  reconciliation) probes it first and delegates per target from a looped
  **include** — never a looped `delegate_to`, which drops the executing host
  from the play when one target is unreachable and silently skips everything
  after it.
- Several roles **de-provision** when their feature is switched off rather than
  leaving inert units behind (archive replication, the ZFS mount anchor). Set
  the enable flag deliberately per host; flipping it off is a live change.

Each scenario's platform image is `${MOLECULE_TEST_IMAGE:-…}` — a FULL image
ref. The scenarios' built-in fallback points at this project's own registry and
its locally-built `:latest` tag; a consumer exports `MOLECULE_TEST_IMAGE` with a
full image ref instead of patching every scenario. From this release the library publishes `molecule-ci` and
`molecule-test` at `:vX.Y.Z` on each release, so a consumer can pin the images
to the same tag it pins the templates to — see
[`docker/README.md`](../docker/README.md). Cross-project registry pulls require
the consumer to be on this project's CI/CD job-token allowlist.

**The fallback only resolves where the images are hosted.** The molecule
templates set `MOLECULE_TEST_IMAGE` in CI, so the scenario fallback is read by
LOCAL runs alone — and a generated consumer's fallback is built from its own
forge and its own path to this library, which only resolves if that forge hosts
the library and its `molecule-ci` / `molecule-test` images. A consumer on
another forge sets `MOLECULE_TEST_IMAGE` for local runs rather than relying on
the fallback.

`molecule-shared/` (the shared scenario base config), each role's `molecule/`
tree and `changelogs/` are `build_ignore`d, so an installed copy carries only
the roles, their metadata and `plugins/`. `plugins/` is an empty scaffold today;
anything added there is FQCN-addressable public API from its first release.

## Internal CI fragments (ci/internal/)

Not a consumer contract. These are this library's own pipeline wiring, kept in
`ci/` (rather than inline in `.gitlab-ci.yml`) only because they are `spec:inputs`
templates. They carry **no parity note and no input-stability guarantee** — see
[VERSIONING.md](VERSIONING.md).

### ci/internal/molecule-matrix.gitlab-ci.yml

The MR-targeted molecule child pipeline: a `molecule-plan` job that runs
`generate-molecule-pipeline.py` over the MR diff and a `molecule-trigger` bridge
that runs the generated child. Defaults target the `weisssrv.infra` collection
(`roles_dir`, `integration_dir`), the privileged runner (`tags`), and a
`jobs_include` file the consumer owns.

The plan job passes `CI_FILE` / `ROLES_DIR` / `INTEGRATION_DIR` /
`MOLECULE_JOBS_INCLUDE` / `MOLECULE_GLOBAL_TRIGGERS` /
`MOLECULE_GLOBAL_TRIGGERS_MODE` to the generator as env —
the same variable names `check-molecule-matrix-coverage.sh` reads, so one set of
inputs configures both gates. Three things the consumer still owns:

- the static full matrix in `ci_file` (its entries are the role inventory),
- `jobs_include` — the `.molecule-test-job` / `.integration-test-job` templates,
  which must be self-contained because a child pipeline includes only that file,
  and must not also define the static matrix the child would collide with,
- the two molecule images (`docker/molecule-{ci,test}/`) and a runner that can
  run privileged DinD.

## Helper scripts (scripts/)

Not CI includes either: the gates and generators a job *runs* — version tracking,
the deploy/molecule coverage invariants, the Flux and Prometheus checks, the
molecule toolkit, the release automation, the B2 drift check. A consumer vendors
the file or calls it from a checkout. Their flags and config-file schemas are
documented in **[SCRIPTS.md](SCRIPTS.md)** with a ready-to-copy config per script
in `examples/`, and they carry the same semver guarantee as a template input.

Site data is always a config file, never a constant in the script: the tracked
service registry, the group→variable export map, the intentionally-unmapped
deploy paths, the chart-native HPA targets, the helm releases to render, and the
B2 bucket identity all live in the consumer's repo.

**A vendored script is a pin too.** Bumping the `include: ref:` does not update
a copy sitting in a consumer's `scripts/` — the CI templates take the script
PATH from the consumer tree, so the copy is what actually runs. All three
consumers gate byte-identity against the library, so a skipped re-vendor fails
their pipeline instead of drifting silently:

| Consumer | Gate | Scope |
|---|---|---|
| weisssrv | `scripts/test_vendored_byte_identity.py` (never skips) | its own manifest, plus one local smoke test — a script sharing a name with a library script must be listed |
| weisssrv-app-template | `tests/validate_render.py:check_registered_copies` | its own manifest — scripts, workflows and lint profiles, on both sides of the copier split |
| weisssrv-cluster-template | `tests/validate_render.py:check_vendored` | the render's `scripts/` + the template repo's own, then its manifest over both |

Every one of them drives
[`scripts/check-vendored-copies.py`](../scripts/check-vendored-copies.py)
against its own `scripts/vendored-manifest.yml`; no hand-maintained
`scripts/`-only list survives. A manifest is what reaches the copies a
`scripts/`-only iterator cannot see — the lint profiles, the vendored GitHub
workflows, `tests/test_check_lib_pins.py` — and the library's offer list
([`scripts/vendorable-paths.yml`](../scripts/vendorable-paths.yml)) bounds what
a manifest may name.

How each reads the library differs, and it matters at release time. weisssrv
passes `--ref` at the pin from its `.gitlab-ci.yml` and falls back to the
checkout's working tree when the tag is not cut yet, announcing the fallback as
a skip rather than assuming it. Both template gates pass `--lib-path` with no
`--ref`, so they compare the library working tree unconditionally. Either way
the lib-merge → tag → consumer-MR order is workable, which is the point. Each
consumer's `scripts/vendored-manifest.yml` lists its vendored files, and
re-vendoring is part of the upgrade procedure in
[VERSIONING.md](VERSIONING.md#upgrading-a-consumer).

## What is NOT here

Deliberately kept in the consumer, because it describes **one** cluster rather
than any cluster:

- **Site data of every kind** — domains, IPs, hostnames, pool names,
  credentials. Anything a second cluster would have to change is an input here,
  never a default.
- **Kubernetes manifests.** They live in the cluster template so a rendered
  cluster is self-contained (no remote kustomize bases pointing back here).
- **weisssrv's own pipeline glue** — `validation-gate`, `test-aggregate-*`,
  `repo-sync-checks`, `repo-policy-checks`, its
  `.gitlab/ci/integration-jobs.yml` multi-role integration matrix, and the
  hermes/camofox image builds (only the reusable DinD pattern is extracted, as
  `ci/build/docker-build.yml`). The per-role molecule matrix is library-owned,
  alongside the roles it runs.
- **The molecule / integration `parallel:matrix` blocks** — their entries ARE the
  consumer's role inventory. The generator that narrows them
  (`generate-molecule-pipeline.py`), the coverage gate over them, the images the
  jobs run in (`docker/molecule-{ci,test}/`) and the plan/trigger wiring
  (`ci/internal/molecule-matrix.gitlab-ci.yml`) are here.

The application-guest Ansible roles (`gitlab`, `plex`, `immich`, `immich_ml`,
`nextcloud`, `home_assistant`) are NOT on this list — they ship in the
collection. Every site value they carry is an asserted input, so they describe
any cluster that wants those services rather than one that has them.
