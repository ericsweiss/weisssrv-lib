# Scripts contract

`scripts/` holds the repo-agnostic gates and generators a consumer vendors (copy
the file) or calls from a checkout of this library. Each is a single file,
stdlib-only unless noted, and takes its **site data from a config file or CLI
flag** — never from constants inside the script.

Everything on this page is part of the semver contract in
[VERSIONING.md](VERSIONING.md): a renamed flag, a changed config key, or a
changed default is a MAJOR bump for scripts, exactly as for a CI template input.

Example configs for every script live in [`../examples/`](../examples/).

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
- **Example:** [`version-registry.example.py`](../examples/version-registry.example.py).

### `version-check-ci.py`

CI wrapper: runs the checker once with `--json`, prints a summary, writes
the report artifact (`--output`, default `version-report.json`; parent dirs
created), and posts/updates an MR comment when there are actionable
(non-held) updates or errors. Exit code mirrors the checker.

- **Env:** `CHECK_VERSIONS_CMD` (default `./scripts/check-versions.py`),
  `CHECK_VERSIONS_LOCAL` (command named in the comment footer),
  `VERSION_CHECK_TIMEOUT` (default 600), `GITLAB_API_TOKEN`.

---

## Release automation

Both are vendored by the consumer (the templates' `script_path` input points at
the copy in the consumer repo) and are stdlib-only.

### `semantic-release.py`

Cuts the tag + GitLab Release from the conventional commits since the last
version tag: `feat` → minor, `fix`/`perf`/`refactor` → patch, `!` or a
`BREAKING CHANGE:` trailer → major (demoted to minor while the version is `0.x`
unless `--major-on-zero`). Tag and Release are created in ONE Releases API call —
that endpoint creates the tag from `ref`, which is the only tag write a
`CI_JOB_TOKEN` can perform. No releasable commit → exit 0, nothing created.

```
semantic-release.py [--repo-dir DIR] [--tag-prefix v] [--initial-version 0.1.0]
    [--major-on-zero] [--ref SHA] [--api-url URL] [--project-id ID]
    [--token-env RELEASE_TOKEN] [--token-header JOB-TOKEN|PRIVATE-TOKEN]
    [--output release.json] [--dry-run]
```

- **Env:** the `--token-env` variable (default `RELEASE_TOKEN`), `CI_API_V4_URL`,
  `CI_PROJECT_ID`, `CI_COMMIT_SHA` (default `--ref`), `CI_PROJECT_URL` (compare
  link in the notes).
- **Artifact** (`--output`): the outcome, not the intention — `released` is true
  only after the API call succeeded, `dry_run` marks a computed-only run, and a
  failure carries an `error` field. Publish it `when: always`.
- **Crash recovery:** a run that dies between the tag and the Release halves
  leaves a tag with no Release, and the next run would compute an empty range
  forever. When the last tag sits on HEAD without a Release, the missing Release
  is created instead.
- **Exit codes:** 0 released / nothing to release / dry run; 1 missing
  credentials, an API failure, a git failure (its stderr is printed) or an
  unreachable API.
- Requires full history + tags (`GIT_DEPTH: 0`).
- Wired by `ci/release/semantic-release.yml`.

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

- **Env:** the `--token-env` variable (default `BOT_TOKEN`; needs `api` +
  `write_repository` — a job token cannot do this), `CI_API_V4_URL`,
  `CI_PROJECT_ID`, `CI_SERVER_HOST` + `CI_PROJECT_PATH` (default `--remote-url`),
  `CI_DEFAULT_BRANCH` (default `--target-branch`), `CI_PIPELINE_URL`.
- **Only tracked changes are committed** (`git add --update`), so report
  artifacts the check command drops stay untracked and out of the MR.
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

---

## CI invariants

### `check-deploy-coverage.sh` (PyYAML for the CI parse)

Fails an MR when a changed Ansible role/playbook/inventory file matches no
deploy job's `changes:` list — a silent no-op deploy. Only jobs whose name starts
with `job_prefix` **and** whose literal `stage:` is `job_stage` get coverage
credit, so a lint job mentioning the same path cannot fake it. Deletions are
excluded (`--diff-filter=d`); an invalid or unrelated base ref exits 2 instead of
reporting "no changes".

- **Config** (`scripts/deploy-coverage.conf`, or `$DEPLOY_COVERAGE_CONFIG`):
  `[settings]` (`roles_dir`, `playbooks_dir`, `inventory_dir`, `ci_file`,
  `job_prefix`, `job_stage`) plus `[roles]` / `[playbooks]` / `[inventory]`
  entries. **Every entry needs a trailing `# rationale`** — the script exits 2
  otherwise, so the "why is this unmapped" rule is machine-enforced rather than
  prose.
- **Example:** [`deploy-coverage.example.conf`](../examples/deploy-coverage.example.conf).

### `check-molecule-matrix-coverage.sh` (PyYAML)

Fails when a molecule scenario dir or an integration-test dir exists with no
matching `parallel:matrix` entry, when a role has no runnable scenario at all,
or when the matrix exceeds `MAX_MATRIX_ENTRIES` (default 45 — an aggregate job
that `needs:` every entry hits GitLab's hard 50-needs-per-job limit).

- **Env:** `CI_FILE`, `ROLES_DIR`, `INTEGRATION_DIR`, `MOLECULE_JOB`,
  `INTEGRATION_JOB`, `UNTESTED_ROLES`, `MAX_MATRIX_ENTRIES`.

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
| `shell-lib.sh` | function-only (safe to source under `set -e`): `timeout_cmd <secs> <cmd…>`, `ssh_probe <target> <cmd>` |
| `find-reachable-host.sh` | prints the first reachable SSH target from its args, exit 1 if none |
| `find-pve-host-for-vm.sh` | prints which Proxmox host runs a VMID (ha-manager → `pvesh /cluster/resources` → per-host `qm status`) |
| `resolve-tool.sh` | prints how to invoke a Python dev tool (`PATH` → `python3 -m <module>` → validated pyenv glob) |

Plus the pre-existing `check-doc-links.py`, `check-taskfile.sh`,
`flux-render.sh`, `kubeconform-skipped.py`.

---

## Tests

Every script above has a suite in `tests/`, run by the library's `python-tests`
job (`python3 -m pytest tests cli/tests`). The suites are consumer-tree
independent: they build throwaway git repos / fixture trees under
`tests/fixtures/` rather than asserting against a real cluster repo.
