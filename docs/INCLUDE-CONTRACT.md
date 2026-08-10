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

Three consumers, and they do not overlap. Read this table before changing an
input default: a default is only "safe" relative to the consumers that take it.

| Template | weisssrv | app-template | cluster-template |
| --- | :-: | :-: | :-: |
| `ci/lint/yaml-lint.yml` | ● | ● | ● |
| `ci/lint/shellcheck.yml` | ● | ● | ● |
| `ci/lint/docs-link-check.yml` | ● | ● | ● |
| `ci/lint/python-lint.yml` | | | ● |
| `ci/lint/ansible-lint.yml` | | | ● |
| `ci/validate/terraform.yml` | ● | (commented) | ● |
| `ci/validate/flux-lint.yml` | ● | ● | ● |
| `ci/security/secret-detection.yml` | ● | ● | ● |
| `ci/test/python-tests.yml` | ● | ● | ● |
| `ci/build/docker-build.yml` | | ● | |
| `ci/review/pr-agent.yml` | | ● | ● |
| `ci/release/semantic-release.yml` | | ● | ● |
| `ci/maintenance/version-check.yml` | ● | | ● |
| `ci/maintenance/version-bump-bot.yml` | | | ● |
| `ci/templates/*` (3 fragments) | | | ● |

**weisssrv consumes eight templates.** Four of them (yaml-lint, shellcheck,
terraform, secret-detection) take the defaults verbatim and share a single
include entry with a `file:` list; the other four (docs-link-check,
version-check, python-tests, flux-lint) each pass their own `inputs:`, which
bind per entry and therefore need an entry each. `docker-build`, `pr-agent`,
`semantic-release`, `version-bump-bot`, `python-lint` and `ansible-lint` are
NOT included by weisssrv — it keeps local equivalents or has no need — so a
change to those six cannot regress weisssrv's pipeline.

## Conventions shared by every template

- **`tags` (array)** — runner tag(s). Default `["infrastructure"]` (weisssrv's
  privileged runner). Tenants and generated clusters pass `tags: []` for a
  shared tag-less runner.
- **`changes` (array)** — the path list used in the merge-request and
  post-merge rules. Every default is a literal, self-contained list stated in
  the template; there is no anchor in any consumer that it must match. Two
  templates ship a default that is deliberately narrower than what weisssrv
  needs, and both say so in their parity note.
- **`default_branch` (string, default `main`)** — the branch the post-merge
  rule compares against, on every template that has such a rule (10 of them).
  It must be a **literal** name. The value is interpolated into a quoted
  `rules:if` string, and GitLab does not expand variables inside quotes, so
  `$CI_DEFAULT_BRANCH` would be compared as literal text and never match — a
  consumer on `master`/`trunk` that left the default would get a job that
  silently stops running after merge. The default reproduces every current
  consumer's behaviour byte-for-byte.
- The rules shape for lint/validate/test jobs is fixed: `schedule → never`,
  `merge_request_event → changes`, `default_branch → changes`, `web`.
- **Every template that DEFINES a job retries it on `runner_system_failure` /
  `scheduler_failure` (max 2).** On a quota-capped shared runner a pipeline's
  fan-out can burst past the namespace quota at pod-creation time; the retry
  turns that hard failure into throttling. Three things are outside that claim:
  the `ci/templates/` fragments define hidden jobs and set no retry (the
  consumer's own job supplies it), the bridge job in `ci/internal/` carries none
  (GitLab rejects `retry` on a bridge), and
  `ci/release/github-release-workflow.example.yml` is a GitHub Actions
  reference copy, not a GitLab job at all.

---

## ci/lint/yaml-lint.yml

- **Reproduces:** weisssrv `yaml-lint`; the same job in both templates.
- **Inputs:** `job_name` (yaml-lint), `stage` (lint), `image`
  (python:3.11-slim), `tags` (["infrastructure"]), `yamllint_version` (1.38.0),
  `config` (`-d relaxed`), `targets`
  (`ansible/ kubernetes/ .gitlab-ci.yml .gitlab/ci/`), `default_branch` (main),
  `changes` (`ansible/**/*`, `kubernetes/**/*`, `.gitlab-ci.yml`,
  `.gitlab/ci/**/*`).
- **Parity:** defaults reproduce weisssrv's four `yamllint -d relaxed <target>`
  invocations (run as a loop over `targets`) and its rules verbatim.
- **A `targets` entry that does not exist is skipped with a note**, not a
  failure — a repo that lacks one of the default trees still passes. But if
  **no** target existed at all the job FAILS: a green job that linted nothing is
  exactly the silent pass this gate exists to prevent.
- **Config profiles:** `lint/yamllint-relaxed.yml` and
  `lint/yamllint-strict.yml` ship here; vendor one and pass `-c <path>`.
- **Tenant:** `inputs: { tags: [], config: "-c .yamllint", targets: "." }`.

## ci/lint/shellcheck.yml

- **Reproduces:** weisssrv `shellcheck`, including the `*.sh.j2` Jinja
  neutralizer.
- **Inputs:** `job_name` (shellcheck), `stage` (lint), `image`
  (koalaman/shellcheck-alpine:v0.10.0), `tags`, `severity` (warning), `exclude`
  (SC1091,SC2034), `direct_globs` (`scripts/*.sh ansible/*.sh`), `find_dir`
  (`ansible/roles`), `default_branch` (main), `changes` (`scripts/**/*`,
  `ansible/*.sh`, `ansible/roles/**/*.sh`, `ansible/roles/**/*.sh.j2`).
- **Parity:** the neutralizer logic (raw-wrapped vs plain templates, the
  rc-accumulating list-file loop) is extracted verbatim; defaults reproduce
  weisssrv's globs and find dir.
- **A `find_dir` that is empty OR absent skips both find loops with a note.**
  Previously only the empty case was handled, so a consumer that simply does
  not have that tree failed on `find`'s non-zero exit. Both live consumers pass
  a directory that exists, so this changes nothing for them.
- **Neutralizer constraint:** in a `{% raw %}`-wrapped template only FULL-LINE
  `{# … #}` comments are stripped (a comment-range delete would eat bash
  `${#arr[@]}`), so keep the out-of-raw header comments of such a template to
  single lines.
- **Tenant:** `inputs: { tags: [], direct_globs: "scripts/*.sh", find_dir: "" }`.

## ci/lint/docs-link-check.yml

- **Reproduces:** weisssrv `docs-link-check`.
- **Inputs:** `job_name` (docs-link-check), `stage` (lint), `image`
  (python:3.11-slim), `tags`, `script_path` (`scripts/check-doc-links.py`),
  `roots` (empty → the checker's own default scope), `default_branch` (main),
  `changes` (`docs/**/*`, `README.md`, `CLAUDE.md`,
  `scripts/check-doc-links.py`, `scripts/test_check_doc_links.py`).
- **Parity — the script halves now agree; the `changes` default still does
  not.** `scripts/check-doc-links.py` here and weisssrv's repo-local copy both
  scan **every git-tracked `*.md` in the repo** (role, app and agent READMEs
  cross-link into `docs/` too), falling back to `docs/` plus
  `$CHECK_DOC_LINKS_EXTRA` only outside a git checkout. That unification is new:
  the library copy previously scanned the narrow docs/+READMEs set, so the two
  copies disagreed about what they were gating. The `changes` **default** was
  not widened with it — it still lists only `docs/`, the two top-level READMEs
  and the checker itself, because a `**/*.md` default would fire the job on
  every consumer's every markdown edit. **A consumer whose markdown lives
  outside `docs/` must pass its own `changes`**, or the widened scan runs on
  fewer merge requests than it covers. weisssrv passes
  `["**/*.md", "scripts/check-doc-links.py", "scripts/test_check_doc_links.py"]`.
- Tenants vendor the stdlib-only checker from `scripts/check-doc-links.py` (no
  network, no dependencies). Re-vendor it at each tag bump: the include contract
  claims the copies are byte-identical, and nothing checks that automatically.
- **Tenant:** `inputs: { tags: [] }` (after vendoring the script).

## ci/lint/python-lint.yml

- **Reproduces:** nothing in weisssrv — the family had no Python linter before
  this template. This library self-applies it; the cluster template includes it.
- **Inputs:** `job_name` (python-lint), `stage` (lint), `image`
  (python:3.11-slim), `tags`, `ruff_version` (0.16.0), `config` (empty → ruff's
  own discovery; pass the FULL argument, e.g. `--config lint/ruff.toml`),
  `targets` (`.`), `default_branch` (main), `format_check` (false), `changes`
  (`**/*.py`, `ruff.toml`, `pyproject.toml`, `.gitlab-ci.yml`).
- **Selection:** the shared profile in `lint/ruff.toml` selects
  `E4,E7,E9,F,W,B` — correctness rules only. Formatting rules (line length,
  quote style, import order) are deliberately excluded: the family has no
  formatter, so they would bury the findings that matter. `format_check` exists
  for a consumer that adopts `ruff format`.
- **Tenant:** `inputs: { tags: [], targets: "src tests" }` (after vendoring
  `lint/ruff.toml`, or with a `ruff.toml` / `[tool.ruff]` of its own).

## ci/lint/ansible-lint.yml

- **Reproduces:** weisssrv's inline `ansible-lint` job. This library
  self-applies it over the `weisssrv.infra` collection; the cluster template
  includes it for the generated repo's own `ansible/` tree.
- **Inputs:** `job_name` (ansible-lint), `stage` (lint), `image`
  (python:3.13-slim), `tags`, `ansible_lint_version` (25.12.2 — keep in step
  with `docker/molecule-ci/requirements.txt` so lint and molecule agree),
  `pip_extra` (`black<26.5.0`, the broken-mypyc-wheel guard), `config` (empty →
  ansible-lint's own discovery; pass the FULL argument, e.g. `-c
  .ansible-lint`), `targets` (`.`), `collections_path` (`.` — exported as
  `ANSIBLE_COLLECTIONS_PATH`, singular ONLY: ansible-compat hard-errors whenever
  the legacy plural spelling is present), `galaxy_requirements` (empty → no
  install; point it at the requirements.yml declaring the dependency collections
  the linted roles' FQCN module refs need), `default_branch` (main), `changes`
  (`**/*.yml`, `**/*.yaml`, `.ansible-lint`, `.ansible-lint-ignore`,
  `.gitlab-ci.yml`).
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
  `.ansible-lint`, or an empty `config` for defaults).

## ci/validate/flux-lint.yml

- **Reproduces:** weisssrv `flux-lint` (substitute mode) and the tenant/cluster
  `flux-lint` (simple mode).
- **Key input `substitute` (boolean, default true):**
  - `true` (weisssrv): extract postBuild vars from a cluster-versions ConfigMap
    (`flux_render_script`), iterate `cluster_dir` Kustomizations, envsubst each,
    kubeconform, run the unvalidated-kind tracker, build the cluster root, then
    run `extra_validation`. Needs a **root** runner (installs `gettext-base`).
  - `false` (tenant): `kustomize build <kustomize_path> | kubeconform …`.
    Non-root safe (stdlib tool download into a workspace `.bin`).
- **Inputs (all modes):** `job_name` (flux-lint), `stage` (lint), `image`
  (python:3.11-slim), `tags`, `substitute` (true), `kubeconform_version`
  (0.6.7) + `kubeconform_sha256`, `kustomize_version` (5.4.3) +
  `kustomize_sha256`, `helm_version` (3.18.4) + `helm_sha256`, `pyyaml_version`
  (6.0.2 — pins the inline `spec.path` parser), `k8s_version` (empty → derived
  from the ConfigMap's `k3s_version`), `default_branch` (main), `changes`
  (`kubernetes/**/*`, `ansible/inventories/prod/group_vars/all.yml`).
- **Substitute-mode inputs:** `cluster_dir` (`kubernetes/clusters/weisssrv`),
  `versions_configmap`
  (`kubernetes/infrastructure/sources/versions-configmap.yaml`),
  `flux_render_script` (`scripts/flux-render.sh`), `skipped_script`
  (`scripts/kubeconform-skipped.py`), `require_cluster_root` (true),
  `extra_validation` (empty).
- **Simple-mode inputs:** `kustomize_path` (`kubernetes/flux`), `k8s_version`.
- **`k8s_version` no longer has a silent fallback.** When it is empty,
  `flux-render.sh k8s-version` derives the schema version from the versions
  ConfigMap's `k3s_version` key — and **fails the job** if that key is absent or
  unparseable, where it previously defaulted to `1.36.0` and validated against a
  version nobody chose. It is also read through a YAML parse now, so a
  `k3s_version` outside `.data` no longer matches. A consumer whose ConfigMap
  lacks the key passes `k8s_version` explicitly.
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
- **Inputs:** `fmt_job_name` (terraform-fmt), `validate_job_name`
  (terraform-validate), `fmt_stage` (lint), `validate_stage` (validate), `image`
  (hashicorp/terraform:1.15), `tags`, `fmt_dir` (`terraform/`), `module_glob`
  (`terraform/*/`), `default_branch` (main), `changes` (`terraform/**/*`).
- **Parity:** defaults reproduce both jobs' script and rules verbatim.
  `default_branch` is applied to BOTH jobs' post-merge rule.
- **Self-applied** over `terraform/modules/` (`module_glob:
  "terraform/modules/*/"`, `validate_stage: lint` — this pipeline has no
  validate stage). The validate loop skips any dir without a `versions.tf`, so a
  `module_glob` that matches nothing passes silently: point it at the level that
  actually holds the modules.

## ci/security/secret-detection.yml

- **Reproduces:** weisssrv `secret_detection`; the same job in both templates.
- **Inputs:** `stage` (security), `tags` (["infrastructure"], set on the
  override job), `cpu_selector` (`esweiss.com/cpu=modern`), `historic_scan`
  (`"false"`), `default_branch` (main), `allow_failure` (**false**).
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
- **`cpu_selector`:** gitleaks' binary needs POPCNT/SSE4.2 and SIGILLs on older
  CPUs. `""` is NOT a "no pin" escape — see the docker-build note below; the
  same runner regex applies.
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
- **Inputs:** `job_name` (build-image), `stage` (build), `image` (python:3.11),
  `tags` (**must be a privileged runner**), `dind_service`
  (`docker:27.5.1-dind@sha256:aa3df78e…`), `docker_cli_version` (27.5.1) +
  `docker_cli_sha256_amd64` / `_arm64`, `buildx_version` (v0.35.0) +
  `buildx_sha256_amd64` / `_arm64`, `registry` (`$CI_REGISTRY_IMAGE`),
  `image_name` (empty → push to the registry base), `context` (`.`),
  `dockerfile` (empty → context Dockerfile), `extra_build_args`,
  `default_branch` (main), `publish_on_main` (true), `changes` (`**/*`),
  `digest_dotenv_var` (empty → off), `digest_dotenv_file` (`image-digest.env`),
  `cpu_selector` (`esweiss.com/cpu=modern`).
- **`dind_service` is digest-pinned and carries an explicit `alias: docker`.**
  The service is a map, not a bare string, because the runner derives the
  network alias from the image name and a digest-bearing name must not be left
  to that derivation — `DOCKER_HOST=tcp://docker:2375` has to resolve. Bumping
  this pin changes the daemon under every build and every molecule job that
  reuses it; treat it as its own change.
- **buildx:** the static docker CLI ships no buildx plugin, so with
  `DOCKER_BUILDKIT=1` the pinned plugin is what makes `docker build` work at
  all. Version + both per-arch sha256s are inputs on the same footing as the
  docker CLI trio (VERSIONING.md's "tool pins are inputs" rule); bump all three
  together, or override them per-consumer for a different buildx.
- **`default_branch` gates BOTH the post-merge rule and the `:latest` publish**,
  and must be a literal name (see the shared conventions above).
  `release_branch` in `ci/release/semantic-release.yml` is literal for the same
  reason.
- **`changes`:** the default `["**/*"]` matches everything — i.e. the
  unconditional rules this template had before the input existed, so an existing
  consumer's resolved rules are unchanged. Narrow it to the image's build
  context (plus anything baked into it) instead of overriding `rules:`
  wholesale: an included job merges key-by-key with a local job of the same
  name, but `rules:` is REPLACED, so a copy silently forks from the template if
  its rules semantics ever change. This library's two image builds each pass
  their own context glob and nothing else, which is what keeps them disjoint.
  The `web` clause stays manual and ungated regardless.
- **`digest_dotenv_var`:** set it to a variable NAME (e.g. `MOLECULE_CI_IMAGE`)
  and the job emits a dotenv report pinning that variable to the pushed image's
  immutable digest ref. A job that `needs:` this one with `artifacts: true` — or
  a `trigger:` bridge that forwards it into a child pipeline — then resolves the
  exact image this pipeline built, instead of a `:latest` anyone can retag
  mid-flight. Left empty the dotenv artifact is still produced but EMPTY, so it
  injects nothing (the report key is static; a missing file would warn on every
  run). Pin only the image a later job runs *as*: an image the job pulls by its
  immutable `:<short-sha>` tag is already pinned.
- **`cpu_selector`:** the job always emits `KUBERNETES_NODE_SELECTOR_CPU`, so
  its value must satisfy the runner's `node_selector_overwrite_allowed` regex.
  On a runner that sets one (both of this instance's do:
  `^esweiss\.com/cpu=(modern|legacy)$`) an EMPTY value fails the check and the
  job errors at pod creation — `""` is not a "no pin" escape. On a runner that
  sets no regex, overwrite is disabled and the value is ignored, so the default
  is safe off-instance; a runner with a different allowlist passes its own value.
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
- **Inputs:** `job_name` (python-tests), `stage` (test), `image`
  (python:3.11-slim), `tags`, `test_dir` (`scripts/`), `pytest_version` (9.1.1),
  `pyyaml_version` (6.0.2), `apt_packages` (`git jq`), `pip_packages` (empty —
  extra pinned pip specs), `setup_command` (`true` — a single command run in
  `before_script` AFTER the apt install and before the pip install, so it may use
  what `apt_packages` provides; this library uses it to clone the app template so
  `test_template_contract.py` cannot silently skip), `default_branch` (main),
  `changes` (`scripts/**/*`, `.gitlab-ci.yml`).
- **Parity:** the junit report, the before_script (apt + pinned pip) and the
  rules are verbatim. The default `changes` is the generic subset only.
  **weisssrv's suite is mostly drift guards that read files outside
  `scripts/`**, so with the default list a guard could not fire on its own
  subject; weisssrv passes a ~28-entry `changes` covering the ansible,
  kubernetes, terraform, docs and Taskfile paths its tests read. Defaults are
  NOT byte-identical for this job. Derive such a list by tracing what the suite
  opens, not by reasoning about what it "should" read.
- **Tenant:** `inputs: { tags: [], apt_packages: "", image: python:3.13,
  test_dir: "tests" }`. `apt_packages` is the one root-only default in the
  library: installing them needs write access to the apt lock, which the shared
  non-privileged runner does not have, so a tenant clears it and picks an image
  that already ships what its tests need (the full `python:3.13` has git).

## ci/review/pr-agent.yml

- **Reproduces:** the `pr-agent-review` job that was copy-pasted into weisssrv,
  this library and the app template — with the 0.40.0 upgrade folded in.
  weisssrv keeps its own local copy and does not include this one.
- **Inputs:** `job_name` (pr-agent-review), `stage` (ai-review), `image`
  (`pragent/pr-agent:0.40.0@sha256:08c42a2b…`, the multi-arch index digest),
  `tags`, `needs` (`[]`), `model` (gpt-5.6), `reasoning_effort` (high),
  `max_model_tokens` (900000), `ai_timeout` (1200),
  `dual_publishing_threshold` (6), `commands` (`review improve`),
  `extra_instructions`, `gitlab_url` (`$CI_SERVER_URL`), `timeout` (45m),
  `secrets_source` (`env` | `1password`), `openai_key` (`$OPENAI__KEY`) /
  `gitlab_token` (`$GITLAB__PERSONAL_ACCESS_TOKEN`) — CI variable REFERENCES for
  env mode, `op_openai_key_ref` / `op_gitlab_token_ref` (1password mode), `gate`
  (the expression that must be non-empty for the job to be created).
- **`gate` takes single quotes.** It lands in a `rules:if` expression; write it
  as the raw expression (`$OPENAI__KEY && $GITLAB__PERSONAL_ACCESS_TOKEN`), not
  pre-quoted.
- **Not byte-identical by design:** the previous copies ran
  `codiumai/pr-agent:0.34` (frozen namespace) on gpt-5.5/`high` and posted **no
  inline comments**. The template drops the obsolete workaround variable
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
- **Inputs:** `job_name` (semantic-release), `stage` (release), `image`
  (python:3.13 — the full image ships git), `tags`, `script_path`
  (`scripts/semantic-release.py`), `tag_prefix` (`v`), `initial_version`
  (0.1.0), `release_branch` (main — a **literal** branch name), `release_token`
  (`$CI_JOB_TOKEN`), `token_header` (JOB-TOKEN), `major_on_zero` (false — a
  breaking change bumps MINOR while 0.x), `dry_run` (false).
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
- **GitHub consumers** (the app template's CI shape B) have no `include:` to
  point at — the library ships no reusable Actions workflows — so they vendor
  [`ci/release/github-release-workflow.example.yml`](../ci/release/github-release-workflow.example.yml)
  as `.github/workflows/release.yml` next to the vendored script. It reproduces
  this job's contract in Actions terms: release branch only and never on a
  schedule, `workflow_run` on the CI workflow succeeding (Actions has no stage
  ordering to gate on), `concurrency` for `resource_group`, `fetch-depth: 0`
  for `GIT_DEPTH: 0`, and `release.json` uploaded `if: always()`. That file is a
  reference copy, NOT a template: nothing `include:`s it, re-vendoring is a
  manual step, and `cli/tests/test_template_contract.py` asserts it stays
  byte-identical to the CLI's scaffold fixture and to the app template's
  vendored copy — so editing it is a coordinated three-repo change.
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
- **Inputs:** `job_name` (version-check), `stage` (**lint**), `image`
  (python:3.11), `tags`, `setup_command` (`true`), `check_command`
  (**required**), `report_path` (`version-report.json`), `default_branch`
  (main), `changes` (`["**/*"]` — NOT `[]`, which matches nothing and would
  delete the job silently). There is deliberately no credential input.
- **Stage split with the bot is deliberate:** this job defaults to `lint` so it
  runs inside a normal MR pipeline, while `version-bump-bot` defaults to
  `maintenance` because it only ever runs on a schedule or a manual web trigger.
  A consumer that puts them in the same stage gets a bot job created on every
  merge request.
- **Retry parity:** this template now carries the same
  `runner_system_failure`/`scheduler_failure` retry as every other job-defining
  template. It previously did not, which is why the shared-conventions claim
  above needed the correction.
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
- **Inputs:** `job_name` (version-bump-bot), `stage` (**maintenance**), `image`
  (python:3.13), `tags`, `script_path` (`scripts/version-bump-mr.py`),
  `setup_command` (`true`), `check_command` (**required** — the command that
  rewrites the pins), `paths` (`.`), `branch` (`bot/version-bumps`),
  `target_branch` (main), `title`, `commit_message`, `labels`, `report_path`
  (embedded in the MR description), `git_user_name` / `git_user_email`,
  `bot_token` (`$VERSION_BUMP_BOT_TOKEN`).
- **`resource_group` + `interruptible: false`.** A schedule and a manual web run
  can no longer race each other on the same force-pushed branch and MR.
- **Token:** a PAT with `api` + `write_repository` — `CI_JOB_TOKEN` cannot push
  and cannot write the Merge requests API. Mask the variable (it goes into the
  push URL; the script also redacts it from its own error output).
- **Rules:** schedules, plus a manual `web` trigger. Untracked files are ignored,
  so a check command that drops a report artifact does not pollute the commit —
  point `report_path` at it instead.
- **`check_command` must exit 0.** A checker that exits 1 when updates exist
  adopts as `check_command: "… || true"`; otherwise the job dies before the bot
  runs.
- **Not self-applied.** `check_command` has no generic value — it is the
  consumer's own version-check run against the consumer's own tracked-version
  config — and this library tracks no upstream versions, so its pipeline does
  not include this template. Alongside `flux-lint` and the `ci/templates/`
  fragments it is first rendered in a consumer; the consumer also owns the
  pipeline schedule that triggers it.

---

## Shared fragments (ci/templates/)

These define hidden jobs; `include` the file, then `extends` or `!reference` the
hidden job. They set no `retry` — the consumer's own job supplies it. Only
`terraform-http-backend.yml` carries `spec:inputs`; the other two take none.

- **dep-cache.yml** → `.dep-cache` (pip + galaxy cache). `extends: .dep-cache`.
- **install-1password.yml** → `.install-1password` / `.install-1password-alpine`.
  `before_script: - !reference [.install-1password, before_script]`. Root runner
  only. Both fragments carry the hardened key verification: the apt fragment
  requires the downloaded material's set of PRIMARY (`pub`) fingerprints to be
  exactly the one expected key (a first-match check would let a bundle smuggle a
  second primary key past `gpg --dearmor`), and the apk fragment verifies in a
  tempfile and only then `install`s into `/etc/apk/keys`.
- **terraform-http-backend.yml** → `.terraform-http-backend` (GitLab HTTP state).
  `extends: .terraform-http-backend`. Inputs: `api_url`
  (`https://git.ericsweiss.com/api/v4`) and `state_name` (`cloudflare`). Both
  default to weisssrv's current values, so an existing consumer's rendered
  `TF_HTTP_*` vars are byte-identical. **A consumer on another GitLab instance
  must pass `api_url`** — `TF_HTTP_PASSWORD` is `${CI_JOB_TOKEN}`, valid only
  against the instance that issued it, so the default address sends that token
  to a foreign host and `terraform init` gets a 401/404; `${CI_API_V4_URL}`
  tracks whichever instance the pipeline runs on. `state_name` sets the
  pipeline's ONE default state (the hidden job's name is fixed, so a second
  include would collide); a job managing a different state overrides the three
  `TF_HTTP_*ADDRESS` vars per-job.

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

The role table, the inventory-wide alias table and the migration entry point are
in the [collection README](../ansible_collections/weisssrv/infra/README.md); the
complete old → new variable map is
[MIGRATING.md](../ansible_collections/weisssrv/infra/MIGRATING.md). Site-specific
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

Each scenario's platform image is
`${MOLECULE_TEST_IMAGE:-…/molecule-test:latest}` — a FULL image ref, so a
consumer that builds or pulls the test image into its own registry exports
`MOLECULE_TEST_IMAGE` (tag or digest included) rather than patching every
scenario. From this release the library publishes `molecule-ci` and
`molecule-test` at `:vX.Y.Z` on each release, so a consumer can pin the images
to the same tag it pins the templates to — see
[`docker/README.md`](../docker/README.md). Cross-project registry pulls require
the consumer to be on this project's CI/CD job-token allowlist.

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
molecule toolkit, the release automation, the B2 drift check. A consumer vendors
the file or calls it from a checkout. Their flags and config-file schemas are
documented in **[SCRIPTS.md](SCRIPTS.md)** with a ready-to-copy config per script
in `examples/`, and they carry the same semver guarantee as a template input.

Site data is always a config file, never a constant in the script: the tracked
service registry, the group→variable export map, the intentionally-unmapped
deploy paths, the chart-native HPA targets, the helm releases to render, and the
B2 bucket identity all live in the consumer's repo.

**A vendored script is a pin too.** Bumping the `include: ref:` does not update
a copy sitting in a consumer's `scripts/`; the two drift silently and nothing
compares them automatically. The per-consumer list of vendored files is in
[CONSUMERS.yml](CONSUMERS.yml), and re-vendoring is part of the upgrade
procedure in [VERSIONING.md](VERSIONING.md#upgrading-a-consumer).

## What is NOT here

Deliberately kept in the consumer, because it describes **one** cluster rather
than any cluster:

- **Site data of every kind** — domains, IPs, hostnames, pool names,
  credentials. Anything a second cluster would have to change is an input here,
  never a default.
- **Kubernetes manifests.** They live in the cluster template so a rendered
  cluster is self-contained (no remote kustomize bases pointing back here).
- **weisssrv's own pipeline glue** — `validation-gate`, `test-aggregate-*`,
  `repo-sync-checks`, `repo-policy-checks`, its generated
  `.gitlab/ci/molecule-jobs.gitlab-ci.yml`, and the hermes/camofox image builds
  (only the reusable DinD pattern is extracted, as `ci/build/docker-build.yml`).
- **The molecule / integration `parallel:matrix` blocks** — their entries ARE the
  consumer's role inventory. The generator that narrows them
  (`generate-molecule-pipeline.py`), the coverage gate over them, the images the
  jobs run in (`docker/molecule-{ci,test}/`) and the plan/trigger wiring
  (`ci/internal/molecule-matrix.gitlab-ci.yml`) are here.

The application-guest Ansible roles (`gitlab`, `plex`, `immich`, `immich_ml`,
`nextcloud`, `home_assistant`) used to be on this list. They are now in the
collection: every site value they carried is an asserted input, so they describe
any cluster that wants those services rather than one that has them.
