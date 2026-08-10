# Scripts contract

`scripts/` holds the repo-agnostic gates and generators a consumer vendors (copy
the file) or calls from a checkout of this library. Each is a single file,
stdlib-only unless noted, and takes its **site data from a config file or CLI
flag** — never from constants inside the script.

Everything on this page is part of the semver contract in
[VERSIONING.md](VERSIONING.md): a renamed flag, a changed config key, or a
changed default is a MAJOR bump for scripts, exactly as for a CI template input.

Example configs for every script live in [`../examples/`](../examples/).

## Forge coupling

**Every script here is forge-neutral except the seven below**, which carry a
**Forge** line in their own section. Neutral means stdlib/PyYAML, the filesystem
and `git` — no forge API, no CI-YAML parsing, no `CI_*` variable it cannot run
without — so a GitHub-hosted consumer runs it unchanged from an Actions step.
(`check-versions.py` calls the GitHub *releases* API as a version SOURCE; that
says nothing about where the consumer is hosted.)

| Script | Forge | Why |
|---|---|---|
| `check-deploy-coverage.sh` | gitlab-only | parses deploy jobs' `changes:` out of `.gitlab-ci.yml`; base ref from `CI_MERGE_REQUEST_DIFF_BASE_SHA` |
| `check-molecule-matrix-coverage.sh` | gitlab-only | parses `parallel:matrix` out of the CI file |
| `generate-molecule-pipeline.py` | gitlab-only | emits a GitLab child-pipeline YAML |
| `check-lib-pins.py` | gitlab-only | its subject is `include:` — GitHub consumers vendor workflows instead |
| `version-bump-mr.py` | gitlab-only | Merge Requests API |
| `semantic-release.py` | dual | `--platform gitlab` (default) or `github` |
| `version-check-ci.py` | neutral core | the report runs anywhere; only the MR comment is GitLab, and it is skipped when the `CI_*` env is absent |

GitHub consumers have no `include:` equivalent for a private library, so they
vendor workflows — see
[`../ci/release/github-release-workflow.example.yml`](../ci/release/github-release-workflow.example.yml)
and the note in [INCLUDE-CONTRACT.md](INCLUDE-CONTRACT.md#who-includes-what).

---

## Version tracking

### `check-versions.py` (PyYAML not required)

Multi-source version discovery: GitHub releases, Docker Hub, GHCR, LinuxServer,
Helm repo indexes, and apt `Packages` indexes, compared against the pins in a
vars file. Disk cache (1 h), bounded retry, Debian version comparison, table +
JSON renderers.

- **Config:** `--config PATH`, else `$CHECK_VERSIONS_CONFIG`, else
  `scripts/version-registry.py` / `.json` under the repo root. `.py` (a module
  defining `CONFIG` or `SERVICE_REGISTRY`) and `.json` are both accepted; the
  Python form keeps each entry's inline rationale.
- **Config keys:** `vars_file`, `services`, `default_deploy_command`,
  `version_file_aliases`, `untracked_allowlist`, `cache_dir`, `repo_root`.
- **Service entry:** `name`, `var_name`, `category`, plus the category's fetch
  fields (`github_repo`, `docker_image`, `ghcr_image`, `helm_repo`/`helm_chart`,
  `apt_url`/`apt_package`), optional `deploy_command`, `version_file`, `held`,
  `notes`, `tag_filter`/`tag_regex`.
- **Modes:** default report (exit 0 clean / 1 updates / 2 errors), `--json`,
  `--service`, `--category`, `--list`, `--update NAME`, `--update-all`,
  `--check-coverage` (fails when a `*_version` pin has no registry entry and is
  not in `untracked_allowlist`), `--no-cache`, `--clear-cache`, `--repo-root`.
- **The CLI is argparse**, so error wording is argparse's
  (`unrecognized arguments: …`, `argument --config: expected one argument`);
  exit code 2 for a usage error is unchanged. `--category` is validated against
  the known set at parse time rather than failing later, and a registry entry
  whose category is unknown lands in an explicit **"Other"** bucket in the table
  instead of vanishing from it.
- **Pins are read AND written anchored at column 0.** An indented `*_version:`
  key is not a pin — it is a nested value in some other structure — so it is
  neither reported as current nor rewritten by `--update`/`--update-all` (both
  report "could not find" for a var that exists only nested). Writes preserve
  the line's existing indentation.
- **Example:** [`version-registry.example.py`](../examples/version-registry.example.py).

### `version-check-ci.py`

CI wrapper: runs the checker once with `--json`, prints a summary, writes
the report artifact (`--output`, default `version-report.json`; parent dirs
created), and posts/updates an MR comment when there are actionable
(non-held) updates or errors. Exit code mirrors the checker.

- **Env:** `CHECK_VERSIONS_CMD` (default `./scripts/check-versions.py`),
  `CHECK_VERSIONS_LOCAL` (command named in the comment footer),
  `VERSION_CHECK_TIMEOUT` (default 600), `GITLAB_API_TOKEN`.
- **Forge: neutral core, GitLab-only comment.** The run + summary + artifact
  need no forge; the comment needs `CI_API_V4_URL` + `CI_PROJECT_ID` +
  `CI_MERGE_REQUEST_IID` + `GITLAB_API_TOKEN` and is skipped (silently outside
  an MR pipeline, with a warning inside one) when they are absent — so a GitHub
  consumer gets the report and no comment.

---

## Release automation

Both are vendored by the consumer (the templates' `script_path` input points at
the copy in the consumer repo) and are stdlib-only.

### `semantic-release.py`

Cuts the tag + Release from the conventional commits since the last
version tag: `feat` → minor, `fix`/`perf`/`refactor` → patch, `!` or a
`BREAKING CHANGE:` trailer → major (demoted to minor while the version is `0.x`
unless `--major-on-zero`). Tag and Release are created in ONE Releases API call —
that endpoint creates the tag from the ref, which on GitLab is the only tag write
a `CI_JOB_TOKEN` can perform. No releasable commit → exit 0, nothing created.

```
semantic-release.py [--platform gitlab|github] [--repo-dir DIR] [--tag-prefix v]
    [--initial-version 0.1.0] [--major-on-zero] [--ref SHA] [--api-url URL]
    [--project-id ID] [--token-env VAR] [--token-header JOB-TOKEN|PRIVATE-TOKEN]
    [--output release.json] [--dry-run]
```

- **Forge: dual.** `--platform gitlab|github`, one vendored copy for both.
- **`--platform`** picks the forge; everything above the two API calls (commit
  parsing, the bump decision, the notes) is forge-neutral, so one vendored copy
  serves both. **`gitlab` is the default**, so a consumer that passes nothing is
  unaffected.

  | | `gitlab` (default) | `github` |
  |---|---|---|
  | create | `POST $CI_API_V4_URL/projects/:id/releases` | `POST $GITHUB_API_URL/repos/:owner/:repo/releases` |
  | probe | `GET …/releases/:tag` | `GET …/releases/tags/:tag` |
  | auth | `JOB-TOKEN:` (or `PRIVATE-TOKEN:`, `--token-header`) | `Authorization: Bearer` + `Accept: application/vnd.github+json` |
  | project | id or `%2F`-escaped path | `:owner/:repo` (the slash is a path separator) |
  | tag | ANNOTATED, carries the notes as its message | LIGHTWEIGHT — the Releases API writes only a ref, so the notes live in the Release body alone |

- **Env**, by platform — a flag always wins over the env:

  | | `gitlab` | `github` |
  |---|---|---|
  | token env (default `--token-env`) | `RELEASE_TOKEN` | `GITHUB_TOKEN` |
  | `--api-url` | `CI_API_V4_URL` | `GITHUB_API_URL` |
  | `--project-id` | `CI_PROJECT_ID` | `GITHUB_REPOSITORY` |
  | `--ref` | `CI_COMMIT_SHA` | `GITHUB_SHA` |
  | compare link in the notes | `CI_PROJECT_URL` | `GITHUB_SERVER_URL` + `GITHUB_REPOSITORY` |

  `--token-header` is GitLab-only; GitHub's auth header is fixed. With no ref in
  the env and none passed, both fall back to `git rev-parse HEAD`.
- **Artifact** (`--output`): the outcome, not the intention — `released` is true
  only after the API call succeeded, `dry_run` marks a computed-only run, and a
  failure carries an `error` field. `recovered` names a tag whose missing
  Release this run backfilled (set even when the new tag then failed), and
  `recovery_check: "failed"` records that the repair check could not run.
  Publish it `when: always`.
- **Crash recovery:** a run that dies between the tag and the Release halves
  leaves a tag with no Release, and the next run would compute an empty range
  forever. The previous tag is checked for a Release wherever it sits; a missing
  one is backfilled from its own commit range before the new tag is cut, so the
  orphaned commits appear in exactly one set of notes. A backfill that fails
  stops the run and says so against ITS tag — the new tag is not cut. The check
  itself is best-effort: an API failure on it is warned about and skipped rather
  than allowed to veto an otherwise-healthy release.
- **Crash recovery on GitHub** works identically, because both halves it needs
  hold there: `GET /releases/tags/:tag` 404s for a tag carrying no *published*
  Release, and creating a Release for a tag that already exists is a plain
  create (`target_commitish` is documented as unused once the tag exists).
  What differs is how the orphan arises — GitHub creates ref and Release in one
  request, so the GitLab half-failure window is not the usual cause. The states
  that do produce it there are ordinary: a `vX.Y.Z` pushed by hand (what a
  GitHub repo did before this backend existed), or a Release deleted while
  GitHub kept its tag. Both land in exactly the same place, and the same repair
  fixes them. One asymmetry: the probe cannot see a *draft* Release, so a draft
  squatting on the tag reads as "missing" and the backfill then fails loudly
  against that tag rather than publishing a second Release for it.
- **Exit codes:** 0 released / nothing to release / dry run; 1 missing
  credentials, an API failure, a git failure (its stderr is printed) or an
  unreachable API.
- Requires full history + tags (`GIT_DEPTH: 0`, or `fetch-depth: 0`).
- Wired by `ci/release/semantic-release.yml` (GitLab) and, for a GitHub
  consumer, by the vendored reference workflow
  [`ci/release/github-release-workflow.example.yml`](../ci/release/github-release-workflow.example.yml).

### `version-bump-mr.py`

Keeps exactly ONE open bot MR in sync with the version pins a consumer-supplied
check command just rewrote. Three idempotent outcomes: bumps with changed
content → force-push the bot branch and create/refresh the MR; bumps with
identical content → nothing (no push, no re-notification); no bumps with an open
bot MR → close it. It never merges.

```
version-bump-mr.py [--repo-dir DIR] [--branch bot/version-bumps]
    [--target-branch main] [--title T] [--commit-message M] [--paths "a/ b/"]
    [--labels a,b] [--report-path FILE] [--git-user-name N] [--git-user-email E]
    [--remote-url URL] [--api-url URL] [--project-id ID] [--token-env BOT_TOKEN]
    [--dry-run]
```

- **Forge: gitlab-only.** The branch half is plain `git`; the MR half is the
  GitLab Merge Requests API, with no `--platform` counterpart.
- **Env:** the `--token-env` variable (default `BOT_TOKEN`; needs `api` +
  `write_repository` — a job token cannot do this), `CI_API_V4_URL`,
  `CI_PROJECT_ID`, `CI_SERVER_HOST` + `CI_PROJECT_PATH` (default `--remote-url`),
  `CI_DEFAULT_BRANCH` (default `--target-branch`), `CI_PIPELINE_URL`.
- **Only tracked changes are committed** (`git add --update`), so report
  artifacts the check command drops stay untracked and out of the MR. Detection
  and staging share one list, read from `git status --porcelain -z` (raw,
  never-quoted paths) and staged with `:(top)`-anchored pathspecs, so a path
  holding non-ASCII characters and a `--repo-dir` below the repo root both work.
- **`--report-path` content is untrusted:** it is fenced with a fence longer than
  any backtick run it contains, lines starting with `/` are indented so GitLab
  cannot read them as quick actions, and truncation cuts on a line boundary.
- A fetch failure is not read as "branch absent" — it fails loudly rather than
  force-pushing and re-notifying.
- **Exit codes:** 0 for every decision above; 1 on missing credentials, an API
  failure, a fetch/push failure (stderr is printed with the token redacted).
- Requires full history (`GIT_DEPTH: 0`) to push.
- Wired by `ci/maintenance/version-bump-bot.yml`.

---

## Generators with a drift gate

Both are idempotent: regenerate in CI and fail if the committed output differs.

### `generate-versions-configmap.py` (PyYAML)

Flattens `*_version` keys (plus each `--nested-key` mapping) from a vars file
into a Flux `postBuild.substituteFrom` ConfigMap. Rejects bool/float/non-scalar
values and any key that is not a valid Flux postBuild identifier.

```
generate-versions-configmap.py --vars-file <in.yml> --output <out.yaml>
    [--name cluster-versions] [--namespace flux-system]
    [--nested-key helm_chart_versions ...] [--regen-command "task flux:sync-versions"]
```

### `generate-hosts-env.py` (PyYAML)

Flattens an Ansible inventory into a shell-sourceable / go-task `dotenv:` file.
Which groups become which variables is an **export map**: entries with
`group` + `value: names|ips|ip` (optionally a single `host:`, and
`required: false`), plus `combine:` entries that union earlier keys in order.

```
generate-hosts-env.py --inventory <hosts.yml> --map <exports.yml>
    [--output <hosts.env>] [--regen-command "task hosts:sync"]
```

- **Example:** [`hosts-env-map.example.yml`](../examples/hosts-env-map.example.yml).

---

## Kubernetes / Flux gates

### `check-hpa-vpa-invariant.py` (PyYAML)

Reads a rendered manifest stream on stdin and fails when an HPA and a mutating
VPA drive the same resource on one workload. With
`--require-chart-native-vpas` it also asserts each declared chart-native HPA
target has a mutating, cpu-excluding VPA, and enforces the no-CPU-limits policy
across pod specs and HelmRelease `.spec.values`.

- **Config:** `--policy-config` with `chart_native_hpa_targets`
  (`namespace`/`kind`/`name`/`source`) and `cpu_limit_allowlist`
  (`namespace/Kind/name`). Both optional; absent = empty.
- **Example:** [`autoscaling-policy.example.yaml`](../examples/autoscaling-policy.example.yaml).
- Wire it as `extra_validation` for `ci/validate/flux-lint.yml`.

### `validate-helm-values.py` (PyYAML, needs `helm`; network)

`kustomize build | kubeconform` never renders a HelmRelease's chart, so
`.spec.values` is unvalidated. This extracts each listed release's values,
substitutes `${...}` from the cluster-versions ConfigMap, and runs
`helm template` (optionally piping to kubeconform). It reuses
`check-hpa-vpa-invariant.py`'s CPU-limit scanner so the kustomize-side and
chart-rendered-side policies cannot diverge.

```
validate-helm-values.py [--kubeconform] [--repo-root DIR] [--releases FILE]
    [--versions-configmap PATH] [--policy-config PATH]
```

- **Releases file** (default `<repo-root>/helm-values-releases.yaml`): a list (or
  a `releases:` mapping) of `{name, manifest, chart, repo_name, repo_url}`.
- **Example:** [`helm-values-releases.example.yaml`](../examples/helm-values-releases.example.yaml).

### `check-kubectl-version-pin.py`

Asserts a CI `kubectl` pin stays within Kubernetes' supported ±1 minor of the
cluster's `k3s_version`. Defaults to `.gitlab-ci.yml` +
`kubernetes/infrastructure/sources/versions-configmap.yaml`; pass both paths
positionally for another layout.

- **Env:** `CI_FILE` retargets the first default (repo-relative or absolute,
  same name as the two molecule scripts), for a consumer whose kubectl pin
  lives somewhere other than `.gitlab-ci.yml`. The extraction is a `dl.k8s.io`
  regex over whatever text it is handed, so the file's format is irrelevant —
  but the two failure messages still name `.gitlab-ci.yml` /
  `versions-configmap.yaml`, which is the conventional layout, not the resolved
  path.

### `extract-prometheus-config.py` + `lint-prometheus-config.sh` (PyYAML)

Extract alert rules from a HelmRelease's `additionalPrometheusRulesMap` and the
Alertmanager config from an ExternalSecret template into standalone files that
`promtool` / `amtool` can lint, then run them plus the promtool alert unit tests.

```
extract-prometheus-config.py rules <out> [--release PATH]
extract-prometheus-config.py alertmanager <out> [--am-config PATH] [--dummy K=V]
```

`lint-prometheus-config.sh` env: `EXTRACT_SCRIPT`, `RULE_TESTS_DIR`,
`HELM_RELEASE`, `AM_CONFIG`. The unit-test step is skipped when `RULE_TESTS_DIR`
holds no `*.test.yaml`.

### `flux-render.sh` (PyYAML)

The two shared halves of `ci/validate/flux-lint.yml`'s substitute mode: turn the
cluster-versions ConfigMap into shell exports, and derive the kubeconform schema
version from it. It does **not** own the per-Kustomization build + kubeconform
loop — that stays in the template.

```
VARS=$(scripts/flux-render.sh export-versions "$CM") || exit 1
eval "$VARS"                                  # every .data key + FLUX_ENVSUBST_VARS
K8S_VER=$(scripts/flux-render.sh k8s-version "$CM")
```

- `export-versions` emits one `export <key>=<shell-quoted value>` per `.data`
  key, plus `FLUX_ENVSUBST_VARS` — the `${name}` allowlist envsubst is given, so
  substitution can never reach a variable the ConfigMap did not declare.
- **Keys are validated before they are emitted, because the caller `eval`s the
  output.** A key that is not a valid POSIX shell name is an error, and so is a
  **reserved** one: `PATH`, `HOME`, `IFS`, `PWD`, `SHELL`, `TMPDIR`, `CI`,
  `CLUSTER_DIR`, `SKIPPED_SCRIPT`, `FLUX_RENDER_SCRIPT`, `VERSIONS_CONFIGMAP`,
  `FAILED`, `RENDER_ALL`, `K8S_VER`, `VARS`, `FLUX_ENVSUBST_VARS`, or anything
  ending `_SHA256`. Those are the calling job's own variables; exporting one
  would rewrite the job's environment mid-run. Generated keys are lowercase, so
  no current consumer trips this.
- `k8s-version` parses `k3s_version` out of `.data` **with PyYAML** and reduces
  it to `major.minor.patch`. A missing or unparseable key is a hard **failure**,
  not a fallback — it previously defaulted to a version nobody chose and
  validated the whole cluster against it. A consumer whose ConfigMap has no such
  key passes the template's `k8s_version` input instead.
- An empty or unreadable ConfigMap path, and a ConfigMap with no `.data`, are
  both errors.
- **Dual-maintained:** weisssrv vendors this file and the flux-lint template
  takes the path from the consumer tree, so the vendored copy is the one CI runs.

### `kubeconform-skipped.py`

Reads `kubeconform -output json` on stdin and prints the distinct
`apiVersion/Kind` pairs kubeconform **skipped** — the CRs whose CRD schema is
absent from the catalog. flux-lint runs kubeconform with
`-ignore-missing-schemas`, so without this a new CRD-backed kind starts shipping
with zero schema validation and no signal at review time.

```
kubeconform ... -output json | scripts/kubeconform-skipped.py
```

Informational only: flux-lint pipes it with `|| true`, and unparseable input
prints a note and exits 0. The per-Kustomization kubeconform passes remain the
actual gate — this only makes the gap visible. Dual-maintained with weisssrv.

---

## Docs and Taskfile gates

### `check-doc-links.py`

Offline checker for relative Markdown cross-links: resolves every relative `.md`
link target against the filesystem and fails on a missing one. A renamed or
deleted doc otherwise rots every link pointing at it, silently.

```
scripts/check-doc-links.py            # scan every tracked *.md in the repo
scripts/check-doc-links.py <root>...  # scan explicit roots
```

- **Scan scope: every *git-tracked* `*.md` in the repo.** Role, app and agent
  READMEs cross-link into `docs/` too, so a docs-only scan gated the wrong half
  of the link graph. Tracked-only is deliberate — untracked scratch Markdown is
  not ours to gate, and including it would make the check fail differently on
  every machine.
- **Fallback (not a git checkout):** everything under `docs/` plus the files
  named in `$CHECK_DOC_LINKS_EXTRA` (default `README.md CLAUDE.md`).
- **What is NOT checked:** URLs, `mailto:`/`tel:`, in-page anchors, and non-`.md`
  targets are all ignored, and the anchor part of a `file.md#section` link is not
  validated — only the file is.
- Stdlib-only and network-free, which is what lets every consumer vendor it.
- **Consumer note:** widening the scan did not widen `ci/lint/docs-link-check.yml`'s
  default `changes` list, which still covers `docs/` + the two top-level READMEs.
  A repo with Markdown elsewhere passes its own `changes` (weisssrv passes
  `**/*.md`) or the job runs on fewer merge requests than it now covers.

### `check-taskfile.sh`

Asserts every `scripts/<name>.{sh,py}` a Taskfile references exists on disk,
plus each `dotenv:` target. go-task compiles command templates lazily and never
stats a referenced file, so a renamed script is invisible to `task --list` — and
a missing dotenv file makes go-task fail hard at load time, taking every task
with it.

```
scripts/check-taskfile.sh [Taskfile.yml]     # default: <repo-root>/Taskfile.yml
```

- **Env:** `CHECK_TASKFILE_DOTENV` — space-separated dotenv targets to require
  when the Taskfile references them. Default `scripts/hosts.env`; set it to the
  consumer's own generated env file(s), or to an empty string for a Taskfile
  with none.
- The dotenv match is on the bare path anywhere in the file, not just a same-line
  `dotenv:`, so the YAML multi-line list form is caught too.
- Dual-maintained with weisssrv.

---

## CI invariants

### `check-lib-pins.py` (PyYAML)

Asserts every `include:` entry pinning this library agrees with the consumer's
single source (`variables.WEISSSRV_LIB_REF`) and that the value is a release
TAG.

The copies are not avoidable: GitLab resolves `include:` at pipeline-CREATION
time, before the `variables:` block exists, so `ref: $WEISSSRV_LIB_REF` silently
does not work. A project/group CI/CD variable *is* readable there, but it moves
the pin out of git, where a bump no longer appears in a diff and cannot be
reverted as an MR. So each entry repeats the tag and this keeps them honest.

Both failures it catches are otherwise silent. A stale ref on one entry runs
that job from a different library version — a changed input default altering the
pipeline with nothing red to show for it. A **branch** ref is worse, and is the
one [VERSIONING.md](VERSIONING.md) forbids: a branch deleted after merge takes
the include with it, and until then the pipeline can change behaviour with no
commit in the consuming repo at all.

- **Forge: gitlab-only** — its subject is the `include:` block. A GitHub
  consumer has no `include:` to drift (it vendors workflows), so the gate has
  nothing to guard there and is simply not wired.
- **Flags:** `--ci-file PATH` (default `<repo root>/.gitlab-ci.yml`),
  `--project` (default `eric/weisssrv-lib`), `--ref-var` (default
  `WEISSSRV_LIB_REF`), `--fix`.
- **`--fix`** rewrites the literals to the single source. A bump is one edit
  plus one command. The rewrite is textual, so comments and formatting survive,
  but the lines it touches come from the PARSED tree — the `ref` key of each
  direct mapping under `include:`, exactly the nodes `check()` reads. Every
  indentation heuristic tried here leaked (`inputs:` may carry its own `project`
  and `ref`), so the two halves agree by construction rather than by scanning.
- **`--fix` refuses rather than half-repairs.** It validates the source value
  before writing anything (a branch would otherwise be propagated to every
  include and only *then* reported), re-parses its own output and requires every
  pin to have landed as the exact string intended, and bounds its targets to the
  include block's own span so an aliased entry cannot redirect it at an anchor
  elsewhere in the file, and it refuses the whole block when an **alias** appears
  inside `include:` — or an anchor DEFINED there, which something outside can
  reference (composing resolves aliases away, so the node tree cannot show
  either). Where it cannot repair — a missing `ref:`, a flow-style
  entry, a pin outside `include:`, an aliased block — it says so and leaves the
  file untouched instead of returning a clean 0. An alias elsewhere in the file
  does not disable it.
- **Exit codes:** 0 consistent, 1 on drift / branch ref / missing variable /
  **no matching include entries at all** — an empty set is reported rather than
  passing, so restructuring the includes out from under the gate is visible.
  **2** for an operator error (unreadable path, malformed YAML, or a top-level
  document that is not a mapping), one line and no traceback, so CI can tell
  "the pins drifted" from "I could not read the file".
- **Handles a `file:` list**, the form that shares one `ref:` across several
  templates, and names every affected template rather than just the entry.
- **Consumers vendor it** and run it from their own tree (their `python-tests`
  job, plus `task lint`). Point that job's `changes` at `.gitlab-ci.yml` so the
  guard fires on its own subject.

### `check-deploy-coverage.sh` (PyYAML for the CI parse)

Fails an MR when a changed Ansible role/playbook/inventory file matches no
deploy job's `changes:` list — a silent no-op deploy. Only jobs whose name starts
with `job_prefix` **and** whose literal `stage:` is `job_stage` get coverage
credit, so a lint job mentioning the same path cannot fake it. Deletions are
excluded (`--diff-filter=d`); an invalid or unrelated base ref exits 2 instead of
reporting "no changes".

- **Forge: gitlab-only.** It reads job names, `stage:` and `changes:` out of
  GitLab CI YAML, and takes its diff base from `CI_MERGE_REQUEST_DIFF_BASE_SHA`
  / `CI_COMMIT_BEFORE_SHA` (a base ref may also be passed as `$1`, which is how
  it runs locally).
- **Config** (`scripts/deploy-coverage.conf`, or `$DEPLOY_COVERAGE_CONFIG`):
  `[settings]` (`roles_dir`, `playbooks_dir`, `inventory_dir`, `ci_file`,
  `job_prefix`, `job_stage`) plus `[roles]` / `[playbooks]` / `[inventory]`
  entries. **Every entry needs a trailing `# rationale`** — the script exits 2
  otherwise, so the "why is this unmapped" rule is machine-enforced rather than
  prose.
- **The check runs one way, deliberately.** It asks "is every changed path
  covered by some deploy job?", never "does every deploy job's `changes:` list
  point at a path that exists?". The reverse direction would fail on a job that
  legitimately guards a path the repo has not created yet, and the failure mode
  it would catch (a dead glob) is inert, where the direction implemented here
  catches the live one: a change that deploys nothing.
- **Example:** [`deploy-coverage.example.conf`](../examples/deploy-coverage.example.conf).

### `check-molecule-matrix-coverage.sh` (PyYAML)

Fails when a molecule scenario dir or an integration-test dir exists with no
matching `parallel:matrix` entry, when a role has no runnable scenario at all,
or when the matrix exceeds `MAX_MATRIX_ENTRIES` (default 45 — an aggregate job
that `needs:` every entry hits GitLab's hard 50-needs-per-job limit).

- **Env:** `CI_FILE`, `ROLES_DIR`, `INTEGRATION_DIR`, `MOLECULE_JOB`,
  `INTEGRATION_JOB`, `UNTESTED_ROLES`, `MAX_MATRIX_ENTRIES`.
- **Forge: gitlab-only** — the disk half is neutral, the matrix it compares
  against is `parallel:matrix` in a GitLab CI file.

### `generate-molecule-pipeline.py` (PyYAML)

Emits a targeted molecule child pipeline for an MR: derives the role dependency
graph from `meta/main.yml` + `include_role`/`import_role` in production dirs and
the stack→roles map from each integration scenario, then selects the affected
scenarios transitively. Fails loudly (exit 2) on a role missing from the matrix
rather than silently under-selecting; any global-trigger path selects everything.

```
generate-molecule-pipeline.py [BASE_SHA | --diff-base SHA | --changed-files-from FILE|-]
    [-o out.yml] [--repo DIR] [--print-graph]
```

- **Forge: gitlab-only** — it reads a GitLab `parallel:matrix` and emits a
  GitLab child-pipeline YAML. The dependency graph it derives (roles →
  scenarios → affected set) is forge-neutral and lives in `compute_affected`.
- **Env:** `CI_FILE`, `ROLES_DIR`, `INTEGRATION_DIR` — repo-relative locations,
  same names as `check-molecule-matrix-coverage.sh`, so one CI `variables:` block
  configures both (e.g. `ROLES_DIR=ansible_collections/<ns>/<name>/roles` for a
  collection layout). `MOLECULE_JOBS_INCLUDE` — the file the generated child
  `include: local:`s. `MOLECULE_GLOBAL_TRIGGERS` — extra global-trigger paths
  (space-separated; a trailing `/` makes it a prefix).
- The collection-root paths that force a full matrix follow `ROLES_DIR` (its
  parent): `requirements.yml`, `galaxy.yml`, `meta/`, `plugins/` and
  `molecule-shared/` — none of them is a role or scenario path, so without this a
  collection-wide change would select nothing and report green. `CI_FILE` and
  `MOLECULE_JOBS_INCLUDE` are triggers too. A repo with no integration suite just
  omits the `integration-tests` job from `CI_FILE`; a job that IS present with a
  broken matrix still fails loudly.
- **`$MOLECULE_GLOBAL_TRIGGERS` must be paired with the plan job's `changes:`.**
  A path listed here forces a full matrix *once the plan job runs* — but if that
  path is not also in the `changes:` list that creates the plan job, an MR
  touching only that path creates no plan job at all and the full matrix never
  happens. The two lists are one setting expressed in two places; change them
  together.
- Wired by `ci/internal/molecule-matrix.gitlab-ci.yml`.

### `molecule-retry.sh`

Runs `molecule test` with an in-job destroy + jittered retry (concurrent
systemd-container starts race cgroup setup). Env: `MOL_MAX` (4), `MOL_BASE`
(args before the subcommand), `MOL_SCEN` (args after it), `JUNIT_OUTPUT_DIR`
(cleared between attempts so only the deciding attempt is reported).

### `sanitize-junit-expected-failures.py`

Downgrades junit `<testcase>` failures whose name matches a substring declared in
the scenario's `expected-junit-failures.txt` (negative-path tests that a
block/rescue handles). Undeclared failures stay red. A missing declaration file
is a no-op.

```
sanitize-junit-expected-failures.py --junit-dir junit --expectations <file>
```

---

## Object storage

### `b2-bucket-drift.py`

Codified settings for a Backblaze B2 bucket (type, SSE, lifecycle rules,
retention) with a drift check and a supervised `--apply` (interactive
confirmation required; a bad lifecycle rule can expire the only offsite copy).

- **Config:** `--config` (default `b2-bucket.json`): `account_id`, `bucket_id`,
  `bucket_name`, `desired`.
- **Env:** `B2_APPLICATION_KEY_ID`, `B2_APPLICATION_KEY`.
- **Example:** [`b2-bucket.example.json`](../examples/b2-bucket.example.json).

---

## Shell helpers

| Script | Contract |
|---|---|
| `shell-lib.sh` | function-only (safe to source under `set -e`): `timeout_cmd <secs> <cmd…>`, `ssh_probe <target> <cmd>`. With neither `timeout` nor `gtimeout` on `PATH` it warns **once per shell** on stderr that probes will run unbounded, rather than silently dropping the bound — anything parsing stderr from the two finders below sees that line |
| `find-reachable-host.sh` | prints the first reachable SSH target from its args, exit 1 if none |
| `find-pve-host-for-vm.sh` | prints which Proxmox host runs a VMID (ha-manager → `pvesh /cluster/resources` → per-host `qm status`) |
| `resolve-tool.sh` | prints how to invoke a Python dev tool (`PATH` → `python3 -m <module>` → validated pyenv glob) |

`find-pve-host-for-vm.sh` env: `PVE_NODE_PREFIX` (default `pve-`) — the prefix
this site's SSH targets carry that the node names the Proxmox API reports do
not. It is applied once to BOTH API-derived answers (`ha-manager status` and
`pvesh get /cluster/resources` report the same bare node identifier); the
per-host `qm status` scan is exempt, since that branch returns a target from the
caller's own list. Set it to `""` when the two already agree; leaving it at the
default on a site whose nodes are named otherwise returns a hostname that does
not resolve.

---

## Tests

Every script above has a suite in `tests/`, run by the library's `python-tests`
job (`python3 -m pytest tests cli/tests`). The suites are consumer-tree
independent: they build throwaway git repos / fixture trees under
`tests/fixtures/` rather than asserting against a real cluster repo, and the
shell scripts are driven through `subprocess` against stub `ssh` / `promtool` /
`amtool` / `molecule` binaries on a controlled `PATH` and a closed environment,
so no cluster, no SSH target and no Prometheus tooling is needed to run them —
and nothing the ambient environment sets can change what they assert.

That sentence is itself gated by `tests/test_scripts_have_tests.py`: it walks
`scripts/` recursively for every executable (plus every `.py`/`.sh`, since
three scripts are vendored rather than run in place) and requires each one to have a
suite that names it, defines tests, and to be mentioned on this page. Opting a
file out means naming it in that file's `EXEMPT` map, with a reason.
