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
  **The `.py` form is imported**, so its top level executes in the job's
  interpreter: it must be repo-owned and reviewed like any other source, and
  `--config` / `$CHECK_VERSIONS_CONFIG` are code-execution inputs. Use the
  `.json` form wherever the config path is not trusted.
- **Config keys:** `vars_file`, `services`, `default_deploy_command`,
  `version_file_aliases`, `untracked_allowlist`, `cache_dir`, `repo_root`,
  `report_title` (heading on the table report; default `Version Check Report`).
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

- **Group-of-groups resolve.** A `group:` naming a group whose members are
  `children:` yields the union of its descendants, depth-first in declaration
  order, first occurrence winning; a cycle terminates. A child defined inline
  under its parent resolves the same as one declared at the top level, and the
  `host:` selector searches the flattened set.
- **An empty resolution names its cause**: the group is absent from the
  inventory, the `host:` is absent from the group, or the group (including its
  children) holds no hosts. `required: false` turns any of the three into an
  empty value instead.
- **Example:** [`hosts-env-map.example.yml`](../examples/hosts-env-map.example.yml).

---

## Kubernetes / Flux gates

### `check-hpa-vpa-invariant.py` (PyYAML)

Reads a rendered manifest stream on stdin and fails when an HPA and a mutating
VPA drive the same resource on one workload. With
`--require-chart-native-vpas` it also asserts each declared chart-native HPA
target has a mutating, cpu-excluding VPA, and enforces the no-CPU-limits policy
across pod specs and HelmRelease `.spec.values`.

The same flag enforces the VPA memory-cap rule, scoped to what each policy
controls: `maxAllowed.memory` **above** the container's limit fails in every
shape (the kubelet would reject the recommendation), and **equal to** it fails
only where the policy also controls limits (`controlledValues: RequestsAndLimits`
or unset, mode not `Off`) — there the updater rescales the limit with the
request, so the ceiling never binds. Under `RequestsOnly` cap == limit is the
correct shape. A VPA whose target workload is not rendered into this
kustomize-only corpus has no limit to compare against and is skipped.

- **Config:** `--policy-config` with `chart_native_hpa_targets`
  (`namespace`/`kind`/`name`/`source`), `cpu_limit_allowlist`
  (`namespace/Kind/name`) and `vpa_cap_allowlist`
  (`namespace/VerticalPodAutoscaler/name`, the grace list for caps not yet
  re-derived). All optional; absent = empty.
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
- **Kube version:** derived from the ConfigMap's `k3s_version` and passed to
  both `helm --kube-version` and `kubeconform -kubernetes-version`. Unlike
  `flux-render.sh k8s-version`, a missing or unparseable key falls back to
  `KUBE_VERSION_FALLBACK` (`1.36.0`) rather than failing, so a malformed pin
  cannot take out `flux:lint` — the rendered chart is then judged against a
  version nobody chose, which is the trade this hook accepts.

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

### `flux-env.sh` (PyYAML, wraps `flux-render.sh`)

The multi-ConfigMap front end to `flux-render.sh` for clusters that substitute
from more than one ConfigMap (versions plus a cluster-config). Same
`export-versions` / `k8s-version` entry points, so callers written for one
ConfigMap keep working with several, plus `merged-configmap` for tools that
accept a single `--versions-configmap`.

```
VARS=$(scripts/flux-env.sh export-versions "$VERSIONS_CM") || exit 1
eval "$VARS"        # every key from every file + ONE merged FLUX_ENVSUBST_VARS
```

- The argument may name several files in one quoted word; later files win on a
  key collision, and a file named twice is read once.
- `FLUX_EXTRA_CONFIGMAPS` (default: the sibling cluster-config path) appends
  files; set it to the empty string to add none.
- `merged-configmap` prints one ConfigMap whose `.data` is the union, with the
  same precedence, so the merged document and the exported environment cannot
  disagree.

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
  not a fallback: validating a whole cluster against a version nobody chose is
  worse than a red job. A consumer whose ConfigMap has no such key passes the
  template's `k8s_version` input instead. Note the two paths that DO fall back
  to `1.36.0` — flux-lint's simple (tenant) mode and
  `validate-helm-values.py`'s `derive_kube_version`.
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

## Cluster invariant gates

Seven gates, in two invocation shapes. **Four read the rendered manifest corpus
on stdin** — `check-pvc-storageclass.py`, `check-scrape-netpol.py`,
`check-default-deny-coverage.py` and `check-secretstore-scope.py` — where the
corpus is what `task flux:lint` accumulates from `kustomize build | envsubst`,
so they wire into `ci/validate/flux-lint.yml`'s `extra_validation` chain.
**Three take paths or flags**: `check-netpol-except-parity.py` (manifest paths),
and `check-alertmanager-behaviour.py` / `check-backup-artifact-apps.py` (flags).
Where a gate needs site data it comes from a flag or a config file, never from
the source, so the shipped file is identical in every consumer —
`check-pvc-storageclass` needs none at all, and `check-secretstore-scope` only
an optional `--external-store`.

### `check-pvc-storageclass.py` (PyYAML)

Fails any PersistentVolumeClaim, StatefulSet `volumeClaimTemplate` or
HelmRelease `persistence` block that sizes a volume without naming a class.
Omitting `storageClassName` is not neutral: the DefaultStorageClass admission
plugin rewrites it at create time to whatever class is default, so the claim
silently binds a dynamically provisioned volume instead of the static PV it was
written for — and a `volumeClaimTemplate` is immutable afterwards.

```
cat rendered-corpus.yaml | scripts/check-pvc-storageclass.py
```

- A HelmRelease values block counts as provisioning when it declares `size` and
  is not `enabled: false`; it satisfies the gate with any of `storageClass`,
  `storageClassName`, `existingClaim` or `existingVolume` (some charts need the
  `"-"` sentinel where an empty string would be dropped by a `with` guard).
- **An empty corpus is an operator error, exit 2**, as it is for the two sibling
  stdin gates. This one takes no arguments at all, so a mis-piped invocation has
  no other symptom. **So is a corpus that arrived but declares no claim** — that
  is what a render loop which never reached the storage-declaring stages
  produces; the success line prints the claim count next to the document count.
- **Exit codes:** 0 clean, 1 on an unpinned claim, 2 on an operator error (an
  empty or claim-less corpus, unparseable input).
- No configuration: the rule is universal.

### `check-scrape-netpol.py` (PyYAML)

Fails a namespace that is scraped AND ingress-restricted but admits no traffic
from the observability namespace. Kubelet probes bypass the CNI policy chain, so
the pod stays healthy while the scrape is REJECTed and the only symptom is
`TargetDown`.

```
cat rendered-corpus.yaml | scripts/check-scrape-netpol.py \
    [--observability-namespace NS] [--exempt NS=REASON ...]
```

- A namespace counts as scraped from a ServiceMonitor/PodMonitor (its own
  namespace, or `spec.namespaceSelector.matchNames`) **or** from a HelmRelease
  whose values enable a chart-native monitor — the case the kustomize corpus
  cannot otherwise see. `namespaceSelector.any` is unattributable and skipped.
- **`--exempt` requires a reason** (`NS=REASON`); an unexplained exemption is
  rejected at parse time.
- **Namespace-level reachability only, so the pass is not proof the scrape
  lands.** Neither half of the policy's own targeting is checked: the port,
  because a chart-native monitor names one that only resolves once the chart is
  rendered; and the policy's `spec.podSelector`, because the pods a monitor
  selects are equally unresolved here. A namespace whose only observability
  allow is attached to some OTHER workload — or admits a port the exporter does
  not listen on — passes this gate with the scrape still REJECTed. The gate
  catches the whole-namespace omission, which is the failure that actually
  recurs; a `TargetDown` that survives a clean run is the signal to check the
  live policy's selector and port.
- **An empty corpus is an operator error, exit 2**, as it is for the two sibling
  stdin gates. **So is a corpus that arrived but holds no scrape target** — the
  observability stage never rendered, so every namespace went unexamined. Scrape
  targets with none ingress-restricted among them is still a pass (default-deny
  is a per-namespace choice); the success line prints both counts.
- **Exit codes:** 0 clean, 1 on a blocked scrape, 2 on an operator error (an
  empty or target-less corpus, unparseable input, a malformed `--exempt`).

### `check-default-deny-coverage.py` (PyYAML)

Fails a namespace that owns a workload but carries no namespace-wide ingress
default-deny. This is the half its sibling `check-scrape-netpol.py` structurally
cannot see: that gate only inspects namespaces which ALREADY run an ingress-deny
policy, so a namespace with no policy at all is invisible to it rather than a
finding.

```
cat rendered-corpus.yaml | scripts/check-default-deny-coverage.py \
    [--exempt NS=REASON ...]
```

- A namespace **owns a workload** when the corpus puts a Deployment /
  StatefulSet / DaemonSet / ReplicaSet / Job / CronJob / Pod in it, **or** a
  HelmRelease targets it — a chart's own workloads never appear in a kustomize
  corpus, so the release is the only visible proxy.
- **A document with no `metadata.namespace` is read as the API reads it: it is
  in `default`.** So a namespace-less Deployment puts `default` in scope and
  `default` must carry a fence like any other namespace; a namespace-less
  NetworkPolicy fences it. (A HelmRelease still honours `spec.targetNamespace`,
  falling back to that defaulted namespace.)
- A namespace is **fenced** by a NetworkPolicy with `Ingress` in `policyTypes`
  and a `podSelector` that selects every pod — absent, `{}`, or the equivalent
  empty-termed spellings `{matchLabels: {}}` / `{matchExpressions: []}` (an
  empty selector term matches everything). An app-scoped policy does not
  count: it fences its own pods and leaves every other pod in the namespace
  open.
- **A namespace-wide policy whose rule names no ports and admits every peer
  counts as wide open, not as a fence.** Both spellings qualify: an empty
  `ingress:` rule (`[{}]` — the API's "from anywhere, on any port"), and a rule
  whose `from` holds a peer that selects everything (`{}`,
  `namespaceSelector: {}`, `podSelector: {}` — an EMPTY label selector matches
  every object in its scope — or a `/0` `ipBlock` whose `except` list leaves
  any address admitted, judged by exact subtraction rather than by assuming
  which ranges a cluster's pods occupy). Peers within a rule are OR'd, so one
  wide peer
  opens the rule whatever else it lists. A rule that names `ports` is never read
  as wide open — it narrows the surface, and port-level policy is a different
  mandate. NetworkPolicies are additive, so one wide-open policy re-opens the
  namespace even with a real `default-deny-ingress` beside it, and the namespace
  is reported unfenced.
- **`flux-system` is the one built-in exemption** — universal to a Flux cluster,
  whose gotk-components manifest ships its own policies and is regenerated
  verbatim by Flux. Every other exemption is site state and arrives as
  `--exempt NS=REASON`, **never as an edit to this file**: it is vendored
  byte-identical, so a local exemption is reverted by the next re-vendor and
  would leak one repo's policy into every other. A reason is mandatory.
- Unused exemptions are printed, never fatal — a namespace can legitimately drop
  out of the corpus.
- **Exit codes:** 0 clean, 1 on an unfenced namespace, 2 on an operator error
  (an empty corpus, a corpus holding no workload namespace at all — the shape a
  render loop that never reached the app stages produces — unparseable input, or
  a malformed `--exempt`).

### `check-secretstore-scope.py` (PyYAML)

Fails a `ClusterSecretStore` with no `spec.conditions` (referenceable from every
namespace, so any ExternalSecret in the cluster can read the whole backing
vault), and any ExternalSecret — or namespace a ClusterExternalSecret fans out
to — sitting in a namespace those conditions do not admit.

```
cat rendered-corpus.yaml | scripts/check-secretstore-scope.py
```

- Condition matching mirrors ESO: a namespace is admitted when ANY condition
  matches, on an exact `namespaces` entry, a `namespaceRegexes` match, or a
  `namespaceSelector` label match. A ClusterExternalSecret's
  `namespaceSelector: {}` is a selector with no terms and therefore matches
  EVERY namespace — absent and empty are not the same thing.
- A ClusterExternalSecret's fan-out is the **union** of `spec.namespaceSelectors`
  (or the deprecated singular `spec.namespaceSelector`) and its literal
  `spec.namespaces` list, the way ESO resolves it. A CES written with the list
  alone matches no selector, so reading the selectors only made its whole
  fan-out invisible to the check.
- **A store referenced but not defined in the corpus FAILS.** That is the runtime
  failure the gate exists to catch: the ExternalSecret never syncs and the Secret
  goes stale. `--external-store NAME` (repeatable) declares a store genuinely
  managed outside the linted tree, so the exemption is visible.
- **An empty corpus is an operator error, exit 2** — a broken pipe or a wrong
  `kustomize build` path must not report green. So is a corpus that HAS documents
  but holds neither a ClusterSecretStore nor a consumer: that is what a render
  loop which never reached the defining stage produces, and it is the likelier of
  the two wiring failures.
- **Exit codes:** 0 clean, 1 on a scoping violation, 2 on an operator error (an
  empty or store-less corpus, unparseable input).
- No configuration file: the rule is universal; `--external-store` is the only
  site-shaped flag.

### `check-netpol-except-parity.py` (PyYAML)

Reads NetworkPolicy manifests from **paths** (not stdin) and asserts no fenced
pod has unrestricted egress, three ways: every egress `ipBlock` /0 peer carries
one of the canonical reserved-CIDR except-lists exactly and in order; no egress
rule reaches a whole fenced range (a /0 written as two /1s, or a lone
`192.168.0.0/16`, are the same escape); and a peer-less egress rule — which
allows every destination — is declared with a reason.

```
scripts/check-netpol-except-parity.py [--config FILE] [path ...]
```

- **Config keys:** `canonical_except_lists` (name -> `[cidr]`, replaces the
  built-in `reserved-full` / `lan-fence` sets **wholesale** — declare both, or
  omit the key), `fence_networks` (the ranges no rule may reach in full;
  defaults to the v4 LAN fence plus `fc00::/7` and `fe80::/10`),
  `unrestricted_egress_ok` (`"<namespace>/<name>"` -> reason).
- **Without `--config` the allowlist is EMPTY**, so a peer-less egress rule
  fails until it is declared. An entry with a blank reason is rejected.
- Ingress is exempt: an unfenced `0.0.0.0/0` ingress peer is a deliberate shape
  (a WAN endpoint).
- **Exit codes:** 0 clean, 1 on a policy violation, 2 on an operator error — a
  path that does not exist, a scanned manifest that does not parse, a run that
  inspected **zero** NetworkPolicy documents, and a `--config` that is missing,
  unparseable or malformed (a bad
  CIDR in `fence_networks`, a reasonless exemption). A renamed manifest subtree
  must not retire the LAN fence quietly; the success line prints the count it
  scanned. Every config arm exits 2, never 1 — otherwise "my config is broken"
  reads as "the fence drifted" and sends the reader into `kubernetes/`.
- **Example:** [`netpol-except.example.yaml`](../examples/netpol-except.example.yaml).

### `check-alertmanager-behaviour.py` (PyYAML, needs `amtool`)

Asserts what the Alertmanager config DOES, not just that it parses. Resolves
each declared route case with `amtool config routes test` and compares the
receiver actually reached; checks every inhibit rule for parseable matchers, a
redundant `equal:` label (which makes the pair dedup nothing), and alertnames
that no longer exist.

```
scripts/check-alertmanager-behaviour.py --config FILE [--repo-root DIR]
                                        [--extract-script PATH]
```

- Extracts the config and rules through the consumer's
  `extract-prometheus-config.py` (default `<repo-root>/scripts/`), so a consumer
  that forked the extractor keeps its own. `--repo-root` is the extractor's
  **cwd** as well as where it is looked up — the extractor resolves its manifest
  defaults relative to the process cwd, so the gate runs from anywhere. Both
  paths are resolved ONCE against the caller's cwd, so a relative `--repo-root`
  is not re-resolved by the child (which would double its prefix), and a
  `--repo-root` that is not a directory exits 2 naming the flag.
- The resolved receiver is compared **exactly**, against the first token of
  amtool's output (it can print several matching receivers in tree order). A
  prefix comparison would pass `critical-page` for an expected `critical`.
- **Config keys:** `route_cases` (required, non-empty; each `receiver` +
  `labels`), `synthetic_route_alerts` (route-case alertnames that deliberately
  name no rule), `upstream_alerts` (alertnames shipped by a chart's own rule
  groups, invisible to the extractor).
- **Every member of a regex alternation is checked**, not just "at least one
  survives", and a regex that is not a plain alternation is REPORTED rather than
  skipped — an empty name set would otherwise pass silently.
- **Exit codes:** 0 clean, 1 on a finding, 2 on an operator error (no amtool, no
  extractor, unreadable or invalid config). The extracted config and rules are
  parsed ONCE up front and a body that is empty, scalar or unparseable is the
  same class. That matters because the extractor copies the `alertmanager.yaml`
  block scalar out of the ExternalSecret **without parsing it**: a typo inside
  that block leaves the outer manifest valid and only surfaces here.
- **Example:** [`alertmanager-behaviour.example.yaml`](../examples/alertmanager-behaviour.example.yaml).

### `check-backup-artifact-apps.py` (PyYAML)

Pairs the `nas_storage` role's `nas_storage_backup_artifact_apps` list with the
`absent(backup_artifact_last_mtime_seconds{app="…"})` arms hand-enumerated in a
`BackupArtifactStale` rule. The two sit on different lifecycles (Ansible deploy
vs Flux reconcile), so both directions rot silently: an app with no arm emits no
series at all when its landing dir is never created, so the freshness arm has
nothing to fire on; an arm with no app fires forever on a series that will never
return. The same split owns `companions:` and its
`BackupArtifactCompanionMissing` rule, checked both ways.

```
scripts/check-backup-artifact-apps.py --host-vars FILE --rules FILE
```

- Both paths are site data, so both flags are required.
- The rule is read as TEXT, scoped to the alert's own block: it lives inside a
  HelmRelease `values:` blob several levels deep, carrying Go-template
  `{{ $labels }}` strings, so a structural walk buys nothing.
- **Exit codes:** 0 in sync, 1 on drift, 2 when either file is missing.

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
scripts/check-taskfile.sh [Taskfile.yml ...]  # default: <repo-root>/Taskfile.yml
```

- **`includes:` are followed.** Both the `name: path.yml` shorthand and the
  `name: {taskfile: path.yml}` map form, resolved relative to the including
  file, with a visited set for cycles and a depth cap
  (`CHECK_TASKFILE_MAX_DEPTH`, default 10). Only a column-0 `includes:` block
  counts. This is what covers the fragments in [`../taskfiles/`](../taskfiles/),
  which carry their own `scripts/` references.
- **A missing include target is a failure**, not a skip: go-task fails hard at
  load time on one, taking every task with it.
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

Asserts every place a consumer pins this library agrees with its single source
(`variables.WEISSSRV_LIB_REF` in `.gitlab-ci.yml`) and that the value is a
release TAG. Two surfaces, one command:

1. every `include:` entry naming this library, and
2. the `weisssrv.infra` collection `version:` in the sibling
   `ansible/requirements.yml`.

`--fix` rewrites both.

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

- **Forge: gitlab-only.** `ansible/requirements.yml` is itself forge-independent,
  but the value it is compared against comes from `variables.WEISSSRV_LIB_REF` in
  `.gitlab-ci.yml`, and an absent CI file exits 2 as an operator error — so on a
  GitHub consumer the gate still cannot run. A GitHub consumer also has no
  `include:` to drift (it vendors workflows), so it is simply not wired there.
- **Flags:** `--ci-file PATH` (default `<repo root>/.gitlab-ci.yml`),
  `--project` (default `eric/weisssrv-lib`), `--ref-var` (default
  `WEISSSRV_LIB_REF`), `--fix`.
- **The collection surface.** The file checked is `<ci-file dir>/ansible/requirements.yml`.
  A repo without one — a tenant app scaffold — is a silent no-op. A
  requirements.yml that installs the library **with no `version:`** is a floating
  pin and fails. The entry is located from the parsed node tree by `--project`
  appearing in its `name:`, so a `version:` under a different collection is never
  matched; that substring match resolves both the Galaxy name and a
  `git+https://…#/ansible_collections/weisssrv/infra` source.
- **`--fix`** rewrites the literals to the single source — both the `include:`
  refs and the collection `version:` — and reports the two counts together
  (`rewrote N pin(s) in <ci-file> + requirements.yml`). A bump is one edit plus
  one command. The rewrite is textual, so comments and formatting survive,
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

### `check-vendored-copies.py` (PyYAML)

Gates a consumer's copies of library files against a library checkout. The copy
relationship is recorded where the copies live — each consumer's own
`scripts/vendored-manifest.yml` — and the library publishes only the OFFER list
([`../scripts/vendorable-paths.yml`](../scripts/vendorable-paths.yml)) of paths
it supports vendoring: a manifest entry outside the offer fails, and a file the
library stops shipping fails every manifest that still names it at the next
bump.

```
scripts/check-vendored-copies.py [--manifest FILE] [--repo-root DIR]
    [--lib-path DIR] [--ref GIT_REF] [--list]
```

- **Two relationships.** `vendored` is byte-identical — drift in either
  direction, a missing local copy, and a file the library dropped all fail.
  `forked` is deliberate divergence: the entry must still DIFFER (a converged
  fork belongs under `vendored`) and, when it records `reconciled_sha256`, the
  LIBRARY side must not have moved since the fork was last reconciled. That last
  arm is what a documentation-only fork list cannot catch.
- **Manifest entry forms:** a bare string when both repos use the same path, or a
  mapping with `lib:` and `consumer:` when they differ (`lint/ruff.toml` ->
  `ruff.toml`; `ci/release/github-release-workflow.example.yml` ->
  `template/{% if ci_shape == 'github' %}.github{% endif %}/workflows/release.yml`
  in the app template, where a copier conditional is a **literal** path segment
  and is written out in full). `reason:` is required on every fork.
- **Scope is not limited to `scripts/`**: lint profiles, vendored test suites
  (the canonical `tests/test_check_lib_pins.py`) and vendored workflows are all
  listable — which is where the unguarded copies were. What a manifest may
  name is bounded by the library's offer list, `scripts/vendorable-paths.yml`.
- **This gate itself is never vendored.** A consumer runs it from a library
  checkout with `--lib-path`; only the files it compares are copies, and the
  offer list deliberately excludes the engine (a copy of the gate would gate
  itself with itself and drift invisibly between pins —
  `tests/test_vendorable_paths.py` pins the exclusion).
- **`--ref`** reads library blobs with `git show <ref>:<path>`. The working-tree
  fallback is decided once **per ref**, not per path: an unresolvable ref means
  the tag is not cut yet (it is cut after the library MR merges), so the run
  compares against the branch it will be tagged from and prints a note saying
  so. When the ref resolves, a path missing at it is reported as "the library no
  longer ships …" — a file added after the tag is not in that release, and
  comparing it against a newer working tree would pass a copy the consumer's pin
  cannot deliver.
- **`reconciled_sha256` lives in the consumer's manifest** and records the
  LIBRARY blob the fork last absorbed. When the library side moves, the fork
  fails until the consumer absorbs the change and re-takes the sha — in its
  own manifest, in the same commit. No library release event is involved.
- **`--lib-path`, else `$WEISSSRV_LIB_PATH`, else `../weisssrv-lib`.** There is
  no skip-when-missing path: an unavailable checkout is an operator error, exit 2.
- **Exit codes:** 0 clean, 1 on drift (or a symlinked/escaping copy), 2 on an
  operator error (malformed or missing manifest, a manifest path outside the
  repo, a missing or malformed offer list, a failing git repository, no
  library checkout).

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
  `MOLECULE_GLOBAL_TRIGGERS_MODE` — `extend` (default) or `replace`; see below.
- The collection-root paths that force a full matrix follow `ROLES_DIR` (its
  parent): `requirements.yml`, `galaxy.yml`, `meta/`, `plugins/` and
  `molecule-shared/` — none of them is a role or scenario path, so without this a
  collection-wide change would select nothing and report green. `CI_FILE` and
  `MOLECULE_JOBS_INCLUDE` are triggers too. A repo with no integration suite just
  omits the `integration-tests` job from `CI_FILE`; a job that IS present with a
  broken matrix still fails loudly.
- **The rest of the default trigger set is the conventional layout, not a
  derivation**: `scripts/molecule-retry.sh`, `scripts/generate-molecule-pipeline.py`,
  `ansible/molecule/`, `ansible/playbooks/maintenance/`, `docker/molecule-test/`
  and `docker/molecule-ci/`. A repo that keeps its image contexts or helpers
  elsewhere would otherwise carry dead triggers *and* a full-matrix fan-out on
  any `docker/` change, so `MOLECULE_GLOBAL_TRIGGERS_MODE=replace` drops that set
  and takes `$MOLECULE_GLOBAL_TRIGGERS` as the whole of it — list your own copy
  of the generator there, or a change to the selection logic stops re-running
  everything. The derived and CI-file entries above are not replaceable. An
  unknown mode value fails the job rather than being ignored.
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
| `shell-lib.sh` | function-only (safe to source under `set -e`): `timeout_cmd <secs> <cmd…>`, `ssh_probe <target> <cmd>`. With neither `timeout` nor `gtimeout` on `PATH` it warns **once per shell** on stderr that probes will run unbounded, rather than silently dropping the bound — anything parsing stderr from a sourcing script (the two finders below here; consumers source it from their own scripts too) sees that line |
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
