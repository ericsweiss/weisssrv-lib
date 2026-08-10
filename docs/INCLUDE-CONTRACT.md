# Include contract

How to consume each CI template, its `spec:inputs`, and the parity note that
records which weisssrv job(s) it reproduces. All templates are included the same
way — by project + pinned ref + file path, with optional `inputs:`:

```yaml
include:
  - project: eric/weisssrv-lib
    ref: v0.2.0            # a release TAG (see VERSIONING.md) — never a branch
    file: /ci/<area>/<template>.yml
    inputs:
      <name>: <value>
```

Conventions shared by every template:

- **`tags` (array)** — runner tag(s). Default `["infrastructure"]` (weisssrv's
  privileged runner). Tenants pass `tags: []` for the shared tag-less runner.
- **`changes` (array)** — the `changes:` path list used in the MR + main rules.
  For most templates (yaml-lint, shellcheck, terraform, flux-lint) the defaults
  are byte-identical to weisssrv's `.paths-*` anchor, so weisssrv gets identical
  rules with default inputs. **Two anchors reach into ansible paths that are
  deliberately out of library scope** (`docs-link-check`, `python-tests`); their
  defaults cover only the generic subset, so on adoption weisssrv passes its own
  full `.paths-docs-links` / `.paths-python-tests` via the `changes` input (see
  each template's Parity note). Tenants pass whatever list fits their repo.
- The rules shape for lint/validate/test jobs is fixed:
  `schedule → never`, `merge_request_event → changes`, `main → changes`, `web`.
- **Every generated job retries on `runner_system_failure` / `scheduler_failure`
  (max 2).** On a quota-capped shared runner a pipeline's fan-out can burst past
  the namespace quota at pod-creation time; the retry turns that hard failure
  into throttling. Bridge jobs carry no retry (GitLab rejects it there).

---

## ci/lint/yaml-lint.yml

- **Reproduces:** weisssrv `yaml-lint`; template `yaml-lint`.
- **Inputs:** `job_name` (yaml-lint), `stage` (lint), `image` (python:3.11-slim),
  `tags` (["infrastructure"]), `yamllint_version` (1.38.0), `config`
  (`-d relaxed`), `targets` (`ansible/ kubernetes/ .gitlab-ci.yml .gitlab/ci/`),
  `changes` (weisssrv `.paths-yaml-lint`).
- **Parity:** defaults reproduce weisssrv's four `yamllint -d relaxed <target>`
  invocations (run as a loop over `targets`) and its rules verbatim.
- **Tenant:** `inputs: { tags: [], config: "-c .yamllint", targets: "." }`.

## ci/lint/shellcheck.yml

- **Reproduces:** weisssrv `shellcheck` (including the `*.sh.j2` Jinja neutralizer).
- **Inputs:** `image` (koalaman/shellcheck-alpine:v0.10.0), `tags`, `severity`
  (warning), `exclude` (SC1091,SC2034), `direct_globs`
  (`scripts/*.sh ansible/*.sh`), `find_dir` (`ansible/roles` — recursively
  shellchecks `*.sh` and neutralizes+checks `*.sh.j2`; empty string skips both
  loops), `changes` (weisssrv `.paths-shellcheck`).
- **Parity:** the neutralizer sed logic (raw-wrapped vs plain templates, the
  rc-accumulating list-file loop) is extracted verbatim; defaults reproduce
  weisssrv's globs + find dir.
- **Tenant:** `inputs: { tags: [], direct_globs: "scripts/*.sh", find_dir: "" }`.

## ci/lint/docs-link-check.yml

- **Reproduces:** weisssrv `docs-link-check`.
- **Inputs:** `script_path` (`scripts/check-doc-links.py`), `roots` (empty →
  checker default of docs/ + top-level READMEs), `changes`.
- **Parity:** weisssrv runs its repo-local `scripts/check-doc-links.py` (byte
  identical). The default `changes` covers the generic subset of weisssrv's
  `.paths-docs-links` (docs/, READMEs, `scripts/check-doc-links.py`,
  `scripts/test_check_doc_links.py`) but **not** `ansible/TESTING.md` — an
  ansible path out of library scope. **Weisssrv adopts by passing its full
  `.paths-docs-links` as `changes`** (or the ansible-only trigger is lost);
  defaults are NOT byte-identical for this job. Tenants vendor the stdlib-only
  checker from this library's `scripts/check-doc-links.py` (no network, no deps).
- **Tenant:** `inputs: { tags: [] }` (after vendoring the script).

## ci/lint/python-lint.yml

- **Reproduces:** nothing in weisssrv — new in v0.2.0; the family had no Python
  linter. This library self-applies it (`.gitlab-ci.yml`).
- **Inputs:** `job_name` (python-lint), `stage` (lint), `image`
  (python:3.11-slim), `tags`, `ruff_version` (0.16.0), `config` (empty → ruff's
  own discovery; pass the FULL argument, e.g. `--config lint/ruff.toml`),
  `targets` (`.`), `format_check` (false), `changes`.
- **Selection:** the shared profile in `lint/ruff.toml` selects
  `E4,E7,E9,F,W,B` — correctness rules only. Formatting rules (line length,
  quote style, import order) are deliberately excluded: the family has no
  formatter, so they would bury the findings that matter. `format_check` exists
  for a consumer that adopts `ruff format`.
- **Tenant:** `inputs: { tags: [], targets: "src tests" }` (after vendoring
  `lint/ruff.toml`, or with a `ruff.toml` / `[tool.ruff]` of its own).

## ci/lint/ansible-lint.yml

- **Reproduces:** weisssrv's inline `ansible-lint` job — new in v0.2.0, and
  this library self-applies it over the `weisssrv.infra` collection.
- **Inputs:** `job_name` (ansible-lint), `stage` (lint), `image`
  (python:3.13-slim), `tags`, `ansible_lint_version` (25.12.2 — keep in step
  with `docker/molecule-ci/requirements.txt` so lint and molecule agree),
  `pip_extra` (`black<26.5.0`, the broken-mypyc-wheel guard), `config` (empty →
  ansible-lint's own discovery; pass the FULL argument, e.g.
  `-c .ansible-lint`), `targets` (`.`), `collections_path` (`.` — exported as
  `ANSIBLE_COLLECTIONS_PATH`, singular ONLY: ansible-compat hard-errors
  whenever the legacy plural spelling is present), `galaxy_requirements`
  (empty → no install; point at the requirements.yml declaring the dependency
  collections the linted roles' FQCN module refs need), `changes`.
- **Non-root safe:** `pip install --user` + absolute user-base path; caches go
  to `$CI_PROJECT_DIR/.ansible-home`.
- **`pip_extra` is routed through a job variable (`PIP_EXTRA`), not
  interpolated into the pip line.** `$[[ inputs.* ]]` is textual substitution
  into the YAML scalar, so a version-ceiling pin — which the default
  `black<26.5.0` is — would render a literal `<` that the shell parses as an
  input redirection *before* any expansion. The result of a parameter expansion
  is word-split but never re-scanned for redirection operators, so the variable
  form takes `<`, `>` and `|` safely. `python-tests` routes `pip_packages` /
  `apt_packages` the same way; pass ceilings freely in either.
- **Tenant:** `inputs: { tags: [], targets: "ansible/" }` (with its own
  `.ansible-lint`, or empty `config` for defaults).

## ci/validate/flux-lint.yml

- **Reproduces:** weisssrv `flux-lint` (substitute mode) and the template
  `flux-lint` (simple mode).
- **Key input `substitute` (boolean, default true):**
  - `true` (weisssrv): extract postBuild vars from a cluster-versions ConfigMap
    (`flux_render_script`), iterate `cluster_dir` Kustomizations, envsubst each,
    kubeconform, run the unvalidated-kind tracker, build the cluster root, then
    run `extra_validation`. Needs a **root** runner (installs `gettext-base`).
  - `false` (tenant): `kustomize build <kustomize_path> | kubeconform ...`.
    Non-root safe (stdlib tool download into a workspace `.bin`).
- **Version inputs (defaults = weisssrv's current pins):** `kubeconform_version`
  (0.6.7) + `_sha256`, `kustomize_version` (5.4.3) + `_sha256`, `helm_version`
  (3.18.4) + `_sha256`.
- **Substitute-mode inputs:** `cluster_dir`
  (`kubernetes/clusters/weisssrv`), `versions_configmap`, `flux_render_script`
  (`scripts/flux-render.sh`), `skipped_script` (`scripts/kubeconform-skipped.py`),
  `k8s_version` (empty → derived from k3s_version), `pyyaml_version` (6.0.2 —
  pins the inline spec.path parser to match weisssrv's `PYYAML_VERSION`),
  `require_cluster_root` (true), `extra_validation` (empty).
- **Cluster-root build (substitute mode) is bootstrap-aware, opt-out.** After
  the per-Kustomization loop the job builds `cluster_dir` itself, to catch a
  malformed top-level `kustomization.yaml`. That root pulls in `flux-system/`,
  whose `gotk-components.yaml` and `gotk-sync.yaml` are written by `flux
  bootstrap` — so before bootstrap they do not exist and the build cannot
  succeed. `require_cluster_root` therefore defaults to **true**: a bootstrapped
  consumer always builds its root, so losing that content fails instead of
  quietly skipping. **A pre-bootstrap consumer must pass
  `require_cluster_root: false`** — weisssrv-cluster-template's generated repo is
  the case — and even then the skip applies only while **both** gotk files are
  absent. If either exists, the root is built so a missing companion file fails
  loudly. A generated repo that omits the input fails its first pipeline; the job
  prints the input to pass. **A bootstrapped cluster is unaffected** — weisssrv
  commits `gotk-components.yaml`, so its root is built either way.
- **Simple-mode inputs:** `kustomize_path` (`kubernetes/flux`), `k8s_version`.
- **Parity:** the render loop, missing-placeholder check, envsubst allowlist, and
  informational skip tracker are extracted from weisssrv. **Full weisssrv parity
  requires passing `extra_validation`** with its HPA/VPA invariant + helm-values
  calls — those reference weisssrv-local scripts (`check-hpa-vpa-invariant.py`,
  `validate-helm-values.py`) that stay in weisssrv. `flux-render.sh` and
  `kubeconform-skipped.py` are shipped here AND kept in weisssrv.
- **Tenant:** `inputs: { tags: [], substitute: false, kubeconform_version:
  "0.8.0", kubeconform_sha256: "...", kustomize_version: "5.8.1",
  kustomize_sha256: "...", k8s_version: "1.36.0" }` (its own newer pins).

## ci/validate/terraform.yml

- **Reproduces:** weisssrv `terraform-fmt` + `terraform-validate` (two jobs).
- **Inputs:** `fmt_job_name`/`validate_job_name`, `fmt_stage` (lint),
  `validate_stage` (validate), `image` (hashicorp/terraform:1.15), `tags`,
  `fmt_dir` (`terraform/`), `module_glob` (`terraform/*/`), `changes`.
- **Parity:** defaults reproduce both jobs' script + rules verbatim.
- **Self-applied** over `terraform/modules/` (`module_glob:
  "terraform/modules/*/"`, `validate_stage: lint` — this pipeline has no
  validate stage). The validate loop skips any dir without a `versions.tf`, so a
  `module_glob` that matches nothing passes silently: point it at the level that
  actually holds the modules.

## ci/security/secret-detection.yml

- **Reproduces:** weisssrv `secret_detection`; template `secret_detection`.
- **Includes** GitLab's `Jobs/Secret-Detection.gitlab-ci.yml` and overrides the
  job. **Inputs:** `stage` (security), `tags` (["infrastructure"], set on the
  override job), `cpu_selector` (`esweiss.com/cpu=modern`), `historic_scan`
  (false), `allow_failure` (**false** — changed in v0.2.0).
- **Parity:** rules (MR/main/schedule) + the node-selector pin are identical to
  both consumers. `allow_failure: false` is what makes the gate real: an
  `allow_failure: true` job counts as SUCCESSFUL for a downstream `needs:`, so
  a gate job that needs this one does **not** block on findings — the pre-0.2.0
  default was advisory in every consumer regardless of its gate wiring. Pass
  `allow_failure: true` for deliberate advisory-only mode (findings still
  produce the security report and a red-flagged job, nothing blocks).
- **cpu_selector:** `""` is not a "no pin" escape — see the docker-build note
  below; the same runner regex applies.
- Pair with `lint/gitleaks.toml` + `lint/secret-detection-ruleset.toml` (vendored
  as `.gitleaks.toml` and `.gitlab/secret-detection-ruleset.toml`). The
  allowlist covers the two published supply-chain pins the CI templates carry
  (1Password's apt GPG fingerprint and apk key sha256), which otherwise trip
  gitleaks' entropy rule and — now that findings block — would fail the job.

## ci/build/docker-build.yml

- **Reproduces:** the DinD `build_and_push` mechanics from weisssrv's
  `.build-molecule-base` (static docker CLI sha-pinned, dind wait, registry layer
  cache + inline cache, bounded retry, `:<sha>` always + `:latest` on main).
- **Inputs:** `job_name` (build-image), `image` (python:3.11), `tags`
  (**must be a privileged runner**), `dind_service` (docker:24.0-dind),
  `docker_cli_version` (27.5.1) + per-arch shas, `buildx_version` (v0.35.0) +
  `buildx_sha256_amd64` / `buildx_sha256_arm64`, `registry`
  (`$CI_REGISTRY_IMAGE`), `image_name` (empty → push to the registry base),
  `context` (.), `dockerfile` (empty → context Dockerfile), `extra_build_args`,
  `default_branch` (main), `publish_on_main` (true), `changes` (`["**/*"]`),
  `digest_dotenv_var` (empty → off) + `digest_dotenv_file`
  (`image-digest.env`), `cpu_selector` (`esweiss.com/cpu=modern` — changed from
  `""` in v0.2.0).
- **buildx:** the static docker CLI ships no buildx plugin, so with
  `DOCKER_BUILDKIT=1` the pinned plugin is what makes `docker build` work at
  all. Version + both per-arch sha256s are inputs on the same footing as the
  docker CLI trio (VERSIONING.md's "tool pins are inputs" rule); bump all three
  together, or override them per-consumer for a different buildx.
- **default_branch:** gates BOTH the post-merge `rules:` clause and the
  `:latest` publish. It must be a **literal** branch name: the value is
  interpolated inside a quoted `rules:if` string, and GitLab does not expand
  variables inside quotes, so `$CI_DEFAULT_BRANCH` is compared as literal text
  and never matches. A consumer on `master`/`trunk` that leaves it at `main`
  gets a job that never runs post-merge: SHA-tagged MR builds keep succeeding
  while `:latest` is never pushed, so `--cache-from` and any `:latest` fallback
  pull silently stop resolving. `release_branch` in
  [ci/release/semantic-release.yml](#cireleasesemantic-releaseyml) is literal
  for the same reason.
- **changes:** the default `["**/*"]` matches everything — i.e. the
  unconditional rules this template had before the input existed, so an
  existing consumer's resolved rules are unchanged. Narrow it to the image's
  build context (plus anything baked into it) instead of overriding `rules:`
  wholesale: an included job merges key-by-key with a local job of the same
  name, but `rules:` is REPLACED, so a copy silently forks from the template if
  its rules semantics ever change. This library's two image builds each pass
  their own context glob and nothing else, which is what keeps them disjoint.
  The `web` clause stays manual and ungated regardless.
- **Note:** the shared tenant runner is non-privileged and CANNOT build — this
  template is weisssrv-only unless the consumer registers a privileged runner.
  weisssrv's molecule / hermes / camofox image builds stay in weisssrv (only the
  reusable pattern is extracted).
- **digest_dotenv_var:** set it to a variable NAME (e.g. `MOLECULE_CI_IMAGE`) and
  the job emits a dotenv report pinning that variable to the pushed image's
  immutable digest ref. A job that `needs:` this one with `artifacts: true` — or
  a `trigger:` bridge that forwards it into a child pipeline — then resolves the
  exact image this pipeline built, instead of a `:latest` anyone can retag
  mid-flight. Left empty the dotenv artifact is still produced but EMPTY, so it
  injects nothing (the report key is static; a missing file would warn on every
  run). Pin only the image a later job runs *as*: an image the job pulls by its
  immutable `:<short-sha>` tag is already pinned.
- **cpu_selector:** the job always emits `KUBERNETES_NODE_SELECTOR_CPU`, so its
  value must satisfy the runner's `node_selector_overwrite_allowed` regex. On a
  runner that sets one (both of this instance's do:
  `^esweiss\.com/cpu=(modern|legacy)$`) an EMPTY value fails the check and the
  job errors at pod creation — `""` is not a "no pin" escape, which is why the
  pre-0.2.0 default was unusable and the only consumer had to override it. On a
  runner that sets no regex, overwrite is disabled and the value is ignored, so
  the new default is safe off-instance; a runner with a different allowlist
  passes its own value.
- **Tenant (with own privileged runner):** `inputs: { tags: [their-runner],
  context: ".", dockerfile: "Dockerfile", cpu_selector: "<their pin>" }`.
- **`:latest` is default-branch-only, deliberately.** An MR build never writes
  it — privileged CI jobs consume that tag, so unreviewed code must not be
  able to populate it. A repo whose default branch has never built the image
  therefore has no `:latest`: bootstrap it with one default-branch pipeline.
  MR pipelines do not need it — they resolve the image they just built through
  `digest_dotenv_var` or the immutable `:<short-sha>` tag.

## ci/test/python-tests.yml

- **Reproduces:** weisssrv `python-tests`.
- **Inputs:** `test_dir` (`scripts/`), `pytest_version` (9.1.1), `pyyaml_version`
  (6.0.2), `apt_packages` (`git jq`), `pip_packages` (empty — extra pinned pip
  specs, added in v0.2.0), `setup_command` (`true` — single command run in
  before_script AFTER the apt install and before the pip install, so it may use
  what `apt_packages` provides; e.g. cloning a sibling repo for a cross-repo
  contract test, which needs git. This library uses it to clone the app template
  so `test_template_contract.py` cannot silently skip), `changes`.
- **Parity:** junit report + before_script (apt + pinned pip) + rules are
  verbatim. The default `changes` is the generic subset (`scripts/**/*`,
  `.gitlab-ci.yml`); weisssrv's `.paths-python-tests` additionally guards four
  ansible paths (`all.yml`, `adguard_home/tasks/api_base_config.yml`,
  `ansible/roles/**/molecule/**/*`, `ansible/integration-tests/**/*`) whose
  pytest suite validates ansible tree files — out of library scope. **Weisssrv
  adopts by passing its full `.paths-python-tests` as `changes`**; defaults are
  NOT byte-identical for this job.
- **Tenant:** `inputs: { tags: [], apt_packages: "", image: python:3.13,
  test_dir: "tests" }`. `apt_packages` is the one root-only default in the
  library: installing them needs write access to the apt lock, which the shared
  non-privileged runner does not have, so a tenant clears it and picks an image
  that already ships what its tests need (the full `python:3.13` has git).

## ci/review/pr-agent.yml

- **Reproduces:** the `pr-agent-review` job that was copy-pasted into weisssrv,
  this library and the app template — with the 0.40.0 upgrade folded in.
- **Inputs:** `job_name` (pr-agent-review), `stage` (ai-review), `image`
  (`pragent/pr-agent:0.40.0@sha256:08c42a2b…`, the multi-arch index digest),
  `tags`, `needs` (`[]`), `model` (gpt-5.6), `reasoning_effort` (high),
  `max_model_tokens` (900000), `ai_timeout` (1200),
  `dual_publishing_threshold` (6), `commands` (`review improve`),
  `extra_instructions`, `gitlab_url` (`$CI_SERVER_URL`), `timeout` (45m),
  `secrets_source` (env | 1password), `openai_key` / `gitlab_token` (CI variable
  REFERENCES for env mode), `op_openai_key_ref` / `op_gitlab_token_ref`
  (1password mode), `gate` (the expression that must be non-empty for the job to
  be created).
- **Not byte-identical by design:** the previous copies ran `codiumai/pr-agent:0.34`
  (frozen namespace) on gpt-5.5/`high` and posted **no inline comments**. The
  template drops the two obsolete workaround variables
  (`CONFIG__CUSTOM_MODEL_MAX_TOKENS`) and the two dead ones
  (`PR_REVIEWER__NUM_CODE_SUGGESTIONS`,
  `PR_CODE_SUGGESTIONS__NUM_CODE_SUGGESTIONS` — removed keys in 0.40), and adds
  `PR_CODE_SUGGESTIONS__DUAL_PUBLISHING_SCORE_THRESHOLD` +
  `CONFIG__PERSISTENT_INLINE_COMMENTS` so suggestions land as committable inline
  discussions without duplicating on re-runs. `allow_failure: true`, the
  schedule exclusion and the MR-only token-gated rule are baked in.
- **Secrets:** `secrets_source: env` reads the two keys from CI/CD variables
  (works on the non-root shared runner). `secrets_source: 1password` reads them
  with `op` at job time and needs a root runner **plus** the op CLI, which the
  consumer adds — the template defines no `before_script`, so this is additive:

  ```yaml
  pr-agent-review:
    before_script:
      - !reference [.install-1password, before_script]
  ```

- **weisssrv:** `inputs: { secrets_source: "1password", gate:
  "$OP_SERVICE_ACCOUNT_TOKEN", needs: [...] }` + the `before_script` override.
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
- **Inputs:** `job_name`, `stage` (release), `image` (python:3.13 — the full
  image ships git), `tags`, `script_path` (`scripts/semantic-release.py`),
  `tag_prefix` (v), `initial_version` (0.1.0), `release_branch` (main — a
  **literal** branch name; it lands in a quoted `rules:if`, which does not
  expand variables),
  `release_token` (`$CI_JOB_TOKEN`), `token_header` (JOB-TOKEN),
  `major_on_zero` (false — a breaking change bumps MINOR while 0.x), `dry_run`.
- **Token:** `CI_JOB_TOKEN` suffices — the Releases API accepts it and creates
  the tag from `ref` (the Tags API itself is read-only for job tokens). Pass a
  PAT reference with `token_header: PRIVATE-TOKEN` if protected tags restrict
  who may create `v*`.
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
- **GitHub consumers** (the app template's CI shape B) have no `include:` to
  point at — the library ships no reusable Actions workflows — so they vendor
  [`ci/release/github-release-workflow.example.yml`](../ci/release/github-release-workflow.example.yml)
  as `.github/workflows/release.yml` next to the vendored script. It reproduces
  this job's contract in Actions terms: release branch only and never on a
  schedule, `workflow_run` on the CI workflow succeeding (Actions has no stage
  ordering to gate on), `concurrency` for `resource_group`, `fetch-depth: 0`
  for `GIT_DEPTH: 0`, and `release.json` uploaded `if: always()`. That file is a
  reference copy, NOT a template: nothing `include:`s it, and re-vendoring when
  it changes is a manual, deliberate step.
- **Requires:** the script vendored at `script_path`, `release` declared as the
  LAST stage (the job sets no `needs:` so stage ordering gates it on the rest of
  the pipeline passing), and a `resource_group` — already set — to serialize
  rapid merges. Artifact: `release.json` — the OUTCOME, not the plan
  (`released` is true only after the API call succeeded; `dry_run` and
  `error` fields mark the other endings). Publish it `when: always`.
- **Self-applied:** this library wires it into its own `.gitlab-ci.yml`
  (`release` stage, `tags: []`), so the tag every consumer pins is cut by the
  merge that earns it and the template is exercised by the MR that changes it.

## ci/maintenance/version-check.yml

- **New capability.** The read-only half of the version pair: reports available
  updates and publishes a report artifact, changing nothing. `version-bump-bot`
  is the other half — it rewrites pins and raises the MR. A repo wants BOTH:
  this one so an MR author sees drift while they are already looking, the bot so
  drift is acted on when nobody is.
- **Inputs:** `job_name` (version-check), `stage` (lint), `image` (python:3.11),
  `tags`, `setup_command` (pip install), `check_command` (**required** — the
  command that reports and writes the report), `report_path`
  (`version-report.json`), `secret_token_command` (optional credential export
  for the MR-comment path, variable-routed), `changes` (MR-rule filter,
  defaulting to `["**/*"]` — NOT `[]`, which matches nothing and would delete
  the job silently).
- **Soft-fail on every trigger, deliberately.** Most checkers signal "updates
  found" with rc=1, which is information rather than a defect — a scheduled or
  MR pipeline must not go red because upstream shipped a release. The artifact
  is published `when: always`, so the report survives whatever the exit code was.
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
- **Degrades rather than fails without credentials.** A FAILING lookup on a
  protected ref is a warning rather than fatal: under `set -e` an unguarded eval
  would end the job before the report it exists to produce. With no token, or a
  broken one, the check still runs and still publishes its artifact.

## ci/maintenance/version-bump-bot.yml

- **New capability.** A scheduled job that runs the consumer's own version-check
  command and keeps exactly ONE bot MR in sync: bumps present → force-push
  `branch` and create or refresh the MR; bumps unchanged from the branch's
  current content → nothing at all (no MR churn on a weekly schedule); no bumps
  with an MR open → close it. It **never merges**.
- **Inputs:** `job_name`, `stage` (maintenance), `image` (python:3.13), `tags`,
  `script_path` (`scripts/version-bump-mr.py`), `setup_command` (`true`),
  `check_command` (**required** — the command that rewrites the pins),
  `paths` (`.`), `branch` (`bot/version-bumps`), `target_branch` (main),
  `title`, `commit_message`, `labels`, `report_path` (embedded in the MR
  description), `git_user_name`/`git_user_email`, `bot_token`
  (`$VERSION_BUMP_BOT_TOKEN`).
- **Token:** a PAT with `api` + `write_repository` — `CI_JOB_TOKEN` cannot push
  and cannot write the Merge requests API. Mask the variable (it goes into the
  push URL; the script also redacts it from its own error output).
- **Rules:** schedules, plus a manual `web` trigger. Untracked files are ignored,
  so a check command that drops a report artifact does not pollute the commit —
  point `report_path` at it instead.
- **`check_command` must exit 0.** weisssrv's `version-check-ci.py` exits 1 when
  updates exist, so it adopts as `check_command: "... || true"`; otherwise the
  job dies before the bot runs.
- **Not self-applied.** `check_command` has no generic value — it is the
  consumer's own version-check run against the consumer's own tracked-version
  config — and this library tracks no upstream versions, so its pipeline does
  not include this template. Alongside `flux-lint` and the `ci/templates/`
  fragments it is first rendered in a consumer; the consumer also owns the
  pipeline schedule that triggers it (a `schedule` clause in `workflow.rules`
  where the consumer restricts them).

---

## Shared fragments (ci/templates/)

These define hidden jobs; `include` the file, then `extends` or `!reference` the
hidden job. Only `terraform-http-backend.yml` carries `spec:inputs` — the other
two take no inputs.

- **dep-cache.yml** → `.dep-cache` (pip + galaxy cache). `extends: .dep-cache`.
- **install-1password.yml** → `.install-1password` / `.install-1password-alpine`.
  `before_script: - !reference [.install-1password, before_script]`. Root runner
  only. Both fragments carry the hardened key verification: the apt fragment
  requires the downloaded material's set of PRIMARY (`pub`) fingerprints to be
  exactly the one expected key (a first-match check would let a bundle smuggle a
  second primary key past `gpg --dearmor`), and the apk fragment verifies in a
  tempfile and only then `install`s into `/etc/apk/keys`.
- **terraform-http-backend.yml** → `.terraform-http-backend` (GitLab HTTP state).
  `extends: .terraform-http-backend`. The one fragment with `spec:inputs`:
  `api_url` (`https://git.ericsweiss.com/api/v4`) and `state_name`
  (`cloudflare`). Both default to weisssrv's current values, so an existing
  consumer's rendered TF_HTTP_* vars are byte-identical. **A consumer on another
  GitLab instance must pass `api_url`** — `TF_HTTP_PASSWORD` is
  `${CI_JOB_TOKEN}`, valid only against the instance that issued it, so the
  default address sends that token to a foreign host and `terraform init` gets a
  401/404; `${CI_API_V4_URL}` tracks whichever instance the pipeline runs on.
  `state_name` sets the pipeline's ONE default state (the hidden job's name is
  fixed, so a second include would collide); a job managing a different state
  overrides the three `TF_HTTP_*ADDRESS` vars per-job.

---

## Terraform modules (terraform/modules/)

Not CI includes, but pinned the same way — a release tag, never a branch:

```hcl
module "zone" {
  source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/cloudflare-zone?ref=v0.2.0"

  account_id = var.cloudflare_account_id
  zone_name  = var.external_domain
  records    = { ... }   # site data
}
```

Each module is a **shape**: resources, defaults and guardrails live here; the
inventory (records, ACL policy, SSO objects) is site data the caller passes in.
Modules declare `required_providers` only — the root module owns the `provider`
block, the backend (`ci/templates/terraform-http-backend.yml`), and the
lockfile.

| Module | Manages | Required inputs |
|---|---|---|
| `cloudflare-zone` | zone settings + DNS records with per-record destroy/drift protection | `account_id`, `zone_name` |
| `tailscale-acl` | tailnet ACL policy + Split-DNS nameservers | `acl_policy`, `split_dns` |
| `authentik-sso` | OAuth2/proxy/SAML providers, applications, groups, policy bindings, embedded outpost | none (every map defaults to `{}`) |

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
    version: v0.2.0        # a release TAG; a branch works for local iteration
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

Role variables are prefixed with the role name and documented in each role's
`README.md`; the collection's own README carries the role table. Site-specific
values (domains, IPs, pool names) are **inputs**, never role defaults — that is
the line between this collection and a cluster instantiation. Where a value is
conventionally inventory-wide and read by several roles, the prefixed variable
is aliased to the conventional name (`base_admin_user: "{{ admin_user |
default('root') }}"`), so a site can set either.

A role that must reach another host (cert distribution, cluster-wide
reconciliation) probes it first and delegates per target from a looped
**include** — never a looped `delegate_to`, which drops the executing host from
the play when one target is unreachable and silently skips everything after it.

Each scenario's platform image is
`${MOLECULE_TEST_IMAGE:-…/molecule-test:latest}` — a FULL image ref, so a
consumer that builds the test image into its own registry exports
`MOLECULE_TEST_IMAGE` (tag or digest included) rather than patching every
scenario.

`molecule-shared/` (the shared scenario base config) and each role's `molecule/`
tree are `build_ignore`d, so an installed copy carries only the roles, their
metadata and `plugins/`. `plugins/` is an empty scaffold today; anything added
there is FQCN-addressable public API from its first release.

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
`MOLECULE_JOBS_INCLUDE` / `MOLECULE_GLOBAL_TRIGGERS` to the generator as env —
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
molecule toolkit, the B2 drift check. A consumer vendors the file or calls it
from a checkout. Their flags and config-file schemas are documented in
**[SCRIPTS.md](SCRIPTS.md)** with a ready-to-copy config per script in
`examples/`, and they carry the same semver guarantee as a template input.

Site data is always a config file, never a constant in the script: the tracked
service registry, the group→variable export map, the intentionally-unmapped
deploy paths, the chart-native HPA targets, the helm releases to render, and the
B2 bucket identity all live in the consumer's repo.

## What is NOT here

Deliberately kept in weisssrv, because they describe **one** cluster rather than
any cluster:

- **Site data of every kind** — domains, IPs, hostnames, pool names,
  credentials. Anything a second cluster would have to change is an input here,
  never a default.
- **Kubernetes manifests.** They live in the cluster template so a rendered
  cluster is self-contained (no remote kustomize bases pointing back here).
- **The eric-specific Ansible roles** — `gitlab`, `plex`, `immich`,
  `immich_ml`, `nextcloud`, `home_assistant`.
- **weisssrv's own pipeline glue** — `validation-gate`, `test-aggregate-*`,
  `repo-sync-checks`, `repo-policy-checks`, its generated
  `.gitlab/ci/molecule-jobs.gitlab-ci.yml`, and the hermes/camofox image builds
  (only the reusable DinD pattern is extracted, as `ci/build/docker-build.yml`).
- **The molecule / integration `parallel:matrix` blocks** — their entries ARE the
  consumer's role inventory. The generator that narrows them
  (`generate-molecule-pipeline.py`), the coverage gate over them, the images the
  jobs run in (`docker/molecule-{ci,test}/`) and the plan/trigger wiring
  (`ci/internal/molecule-matrix.gitlab-ci.yml`) are here.
