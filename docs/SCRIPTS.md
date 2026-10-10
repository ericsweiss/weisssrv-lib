# Scripts contract

`scripts/` holds the repo-agnostic gates and generators a consumer vendors (copy
the file) or calls from a checkout of this library. Each is a single file,
stdlib-only unless noted, and takes its **site data from a config file or CLI
flag** — never from constants inside the script.

Everything on this page is part of the semver contract in
[VERSIONING.md](VERSIONING.md): a renamed flag, a changed config key, or a
changed default is a MAJOR bump for scripts, exactly as for a CI template input.

Example configs for every script live in [`../examples/`](../examples/).

## Index

| Script | Does | Config | Forge |
|---|---|---|---|
| [`b2-bucket-drift.py`](#b2-bucket-driftpy) | codified B2 bucket settings, drift check + supervised `--apply` | `b2-bucket.example.json` | neutral |
| [`check-alertmanager-behaviour.py`](#check-alertmanager-behaviourpy-pyyaml-needs-amtool) | asserts what the Alertmanager config routes, inhibits and silences | `alertmanager-behaviour.example.yaml` | neutral |
| [`check-ci-include-job-names.py`](#check-ci-include-job-namespy-pyyaml) | an optional `needs:` naming a job no include creates | flags | gitlab-only |
| [`check-backup-artifact-apps.py`](#check-backup-artifact-appspy-pyyaml) | pairs the backup-artifact app list with its alert rule, both ways | flags | neutral |
| [`check-cluster-invariants.py`](#check-cluster-invariantspy-pyyaml) | duplicate vmid / address, a host on a VIP, a host outside the LAN CIDR, an alert-rule `instance=` literal that is no inventory address | flags | neutral |
| [`check-comment-length.py`](#check-comment-lengthpy) | a comment block longer than three content lines | flags or YAML | neutral |
| [`check-dashboards.py`](#check-dashboardspy-pyyaml) | Grafana dashboard JSON plus its configMapGenerator registration | flags | neutral |
| [`check-default-deny-coverage.py`](#check-default-deny-coveragepy-pyyaml) | a namespace owning a workload with no ingress default-deny | `--exempt` | neutral |
| [`check-deploy-coverage.sh`](#check-deploy-coveragesh-pyyaml-for-the-ci-parse) | a changed Ansible path matching no deploy job's `changes:` | `deploy-coverage.example.conf` | gitlab-only |
| [`check-deploy-preflight.py`](#check-deploy-preflightpy-pyyaml) | a deploy job whose `--tags` selection, under its own `--skip-tags`, would run no task | flags | gitlab-only |
| [`check-doc-links.py`](#check-doc-linkspy) | relative Markdown cross-links resolve | env | neutral |
| [`check-flux-stage-timeouts.py`](#check-flux-stage-timeoutspy-pyyaml) | a waiting Flux stage that does not outlast the releases beneath it | flags | neutral |
| [`check-flux-version-pin.py`](#check-flux-version-pinpy) | the Flux CLI pin, the versions ConfigMap and gotk-components agree | flags | neutral |
| [`check-helm-repo-parity.py`](#check-helm-repo-paritypy-pyyaml) | chart-repo URLs equal to the HelmRepository CRs Flux pulls from | flags | neutral |
| [`check-helm-values-coverage.py`](#check-helm-values-coveragepy-pyyaml) | a HelmRelease the helm-values registry neither lists nor excludes | flags | neutral |
| [`check-helmrelease-crd-safety.py`](#check-helmrelease-crd-safetypy-pyyaml) | a CRD-owning HelmRelease that would delete its CRDs on uninstall | `helmrelease-crd-safety.example.yaml` | neutral |
| [`check-hpa-vpa-invariant.py`](#check-hpa-vpa-invariantpy-pyyaml) | HPA/VPA overlap, CPU limits, VPA caps, 1:1 memory ratios, a request above its own limit | `autoscaling-policy.example.yaml` | neutral |
| [`check-ephemeral-storage-cap.py`](#check-ephemeral-storage-cappy-pyyaml) | a sized emptyDir larger than its container's ephemeral-storage limit | stdin | neutral |
| [`check-include-contract.py`](#check-include-contractpy-pyyaml) | an undeclared `inputs:` key, an omitted REQUIRED input, a job stage the pipeline does not declare | flags | gitlab-only |
| [`check-ingressroute-backends.py`](#check-ingressroute-backendspy-pyyaml) | a route backend or scrape port that resolves to no Service in the corpus | stdin | neutral |
| [`check-issuer-refs.py`](#check-issuer-refspy-pyyaml) | a cert-manager issuer reference that resolves to nothing the corpus ships | `--allow-external` | neutral |
| [`check-kubectl-version-pin.py`](#check-kubectl-version-pinpy) | a CI kubectl pin within ±1 minor of the cluster | env | neutral |
| [`check-kustomization.py`](#check-kustomizationpy-pyyaml) | a manifest no kustomization.yaml lists, and a list that renders nothing | paths | neutral |
| [`check-lib-pins.py`](#check-lib-pinspy-pyyaml) | every library pin equals `WEISSSRV_LIB_REF` and is a release tag | flags | gitlab-only |
| [`check-live-cpu-limits.py`](#check-live-cpu-limitspy-pyyaml-via-gate-common) | a live container running with a CPU limit, and live/template memory-limit drift | none | neutral |
| [`check-molecule-image-pin.py`](#check-molecule-image-pinpy-pyyaml) | every hand-written molecule test-image tag equals the same pin | flags | gitlab-only |
| [`check-molecule-matrix-coverage.sh`](#check-molecule-matrix-coveragesh-pyyaml) | molecule scenarios and matrix entries agree, both directions | env | gitlab-only |
| [`check-netpol-except-parity.py`](#check-netpol-except-paritypy-pyyaml) | no fenced pod has unrestricted egress | `netpol-except.example.yaml` | neutral |
| [`check-nfs-tls.py`](#check-nfs-tlspy-pyyaml) | an NFS PV that does not mount over TLS by hostname | flags | neutral |
| [`check-prometheus-rule-coverage.py`](#check-prometheus-rule-coveragepy-pyyaml) | a shipped alert with no promtool unit test and no declared exemption | flags | neutral |
| [`check-pvc-storageclass.py`](#check-pvc-storageclasspy-pyyaml) | a sized volume with no storage class named | none | neutral |
| [`check-role-defaults-documented.py`](#check-role-defaults-documentedpy-pyyaml) | a role default its README never names | flags | neutral |
| [`check-role-inputs.py`](#check-role-inputspy-pyyaml-jinja2) | inventory vs the collection's opt-in and required-input conventions | flags | neutral |
| [`check-role-readme-literals.py`](#check-role-readme-literalspy) | one site's addresses or domains in a role README | flags | neutral |
| [`check-rule-windows.py`](#check-rule-windowspy-pyyaml) | an alert `for:` hold that is not inside its accumulating lookback | flags | neutral |
| [`check-runbook-anchors.py`](#check-runbook-anchorspy) | an alert `runbook_url` pointing at a missing doc or a dangling anchor | flags | neutral |
| [`check-scrape-netpol.py`](#check-scrape-netpolpy-pyyaml) | a scraped, fenced namespace that admits no observability traffic, or a monitor port nothing declares | `--exempt` | neutral |
| [`check-scrape-wiring.py`](#check-scrape-wiringpy-pyyaml) | a scrape NetworkPolicy that does not admit the port its monitor scrapes | paths | neutral |
| [`check-secret-rotation-coverage.py`](#check-secret-rotation-coveragepy-pyyaml) | an ESO-managed credential with no documented rotation path | flags | neutral |
| [`check-secretstore-scope.py`](#check-secretstore-scopepy-pyyaml) | an unscoped ClusterSecretStore, or a consumer outside its conditions | `--external-store` | neutral |
| [`check-taskfile.sh`](#check-taskfilesh) | every script, dotenv and task a Taskfile references exists, and no fragment is orphaned | env | neutral |
| [`check-vendored-copies.py`](#check-vendored-copiespy-pyyaml) | a consumer's vendored and forked copies against a library checkout | the consumer's manifest | neutral |
| [`check-version-checksums.py`](#check-version-checksumspy-pyyaml) | every checksum pin the version registry declares (network) | the version registry | neutral |
| [`check-versions.py`](#check-versionspy-pyyaml-not-required) | multi-source version discovery against a repo's pins | `version-registry.example.py` | neutral |
| [`ci-fetch-tools.py`](#ci-fetch-toolspy) | installs the pinned CI tool binaries into a workspace bin, sha256-verified (network) | env | neutral |
| `ci_yaml.py` | importable loader for a CI file using GitLab's `!` tags | n/a | gitlab-only |
| `cluster-config-value.sh` | prints values from the cluster-config ConfigMap | env | neutral |
| [`extract-prometheus-config.py`](#extract-prometheus-configpy-lint-prometheus-configsh-pyyaml) | extracts rules and Alertmanager config for promtool/amtool | flags | neutral |
| `find-pve-host-for-vm.sh` | prints which Proxmox host runs a VMID | env | neutral |
| `find-reachable-host.sh` | prints the first reachable SSH target from its args | args | neutral |
| [`flux-child-kustomizations.py`](#flux-child-kustomizationspy-pyyaml) | child Flux Kustomizations in `dependsOn` order | flags | blocking on a cycle |
| [`flux-env.sh`](#flux-envsh-pyyaml-wraps-flux-rendersh) | multi-ConfigMap front end to `flux-render.sh` | env | neutral |
| [`flux-render.sh`](#flux-envsh-pyyaml-wraps-flux-rendersh) | ConfigMap → shell exports, and the kubeconform schema version | args | neutral |
| [`gate_common.py`](#gate-commonpy-pyyaml-not-run-directly) | shared loader, cluster-config, key and NetworkPolicy helpers for the corpus gates | n/a | neutral |
| [`generate-hosts-env.py`](#generate-hosts-envpy-pyyaml) | inventory → shell/dotenv file, with a drift gate | `hosts-env-map.example.yml` | neutral |
| [`generate-molecule-pipeline.py`](#generate-molecule-pipelinepy-pyyaml) | targeted molecule child pipeline for an MR | env | gitlab-only |
| [`generate-versions-configmap.py`](#generate-versions-configmappy-pyyaml) | vars file → Flux substitution ConfigMap, with a drift gate | flags | neutral |
| [`kubeconform-skipped.py`](#kubeconform-skippedpy) | unvalidated-kind tracker, or a floor against a baseline | baseline file | neutral |
| [`lint-prometheus-config.sh`](#extract-prometheus-configpy-lint-prometheus-configsh-pyyaml) | runs promtool/amtool over the extracted config | env | neutral |
| [`maintenance-run-with-verify.sh`](#maintenance-run-with-verifysh) | runs a maintenance command, then the verify script whatever the outcome | env | neutral |
| [`molecule-retry.sh`](#molecule-retrysh) | `molecule test` with an in-job destroy + jittered retry | env | neutral |
| `ci-run-check.sh` | sourceable `run_check` / `run_check_summary` driver for a consolidated gate job | n/a | neutral |
| `resolve-tool.sh` | prints how to invoke a Python dev tool | args | neutral |
| [`run-render-gates.sh`](#run-render-gatessh) | runs a consumer's ordered corpus-gate list | `render-gates.example.conf` | neutral |
| [`sanitize-junit-expected-failures.py`](#sanitize-junit-expected-failurespy) | downgrades declared negative-path junit failures | expectations file | neutral |
| [`supervised-apply-guard.sh`](#supervised-apply-guardsh) | confirmation ceremony for a supervised apply | env | neutral |
| [`semantic-release.py`](#semantic-releasepy) | cuts the tag + Release from conventional commits | flags | dual |
| `shell-lib.sh` | sourceable `timeout_cmd` / `ssh_probe` / `kubectl_read` / `captured_match` / `url_contains` / `ssh_contains` helpers | env | neutral |
| `smoke-lib.sh` | sourceable HTTP/TCP probe classifiers and a PASS/FAIL ledger for a consumer's per-guest verify scripts | env | neutral |
| [`wait-for-reloader-roll.sh`](#wait-for-reloader-rollsh) | waits for Reloader to roll a Deployment after its ConfigMap was patched | args | neutral |
| `deploy-verify-lib.sh` | sourceable node, pod and HelmRelease readiness classifiers for a deploy-verify driver | n/a | neutral |
| `collect-state-lib.sh` | sourceable redaction, section-emitter and cluster-verdict helpers | n/a | neutral |
| [`unifi-settings-drift.py`](#unifi-settings-driftpy) | a console-owned UniFi settings section drifting from its codified expectation | `unifi-settings.example.json` | neutral |
| [`validate-helm-values.py`](#validate-helm-valuespy-pyyaml-needs-helm-network) | renders each HelmRelease's chart and validates it | `helm-values-releases.example.yaml` | neutral |
| [`version-bump-mr.py`](#version-bump-mrpy) | keeps one open bot MR in sync with the version pins | flags | gitlab-only |
| [`version-check-ci.py`](#version-check-cipy) | CI wrapper: report artifact + refreshed MR comment | the version registry | neutral core |

## Forge coupling

Neutral means stdlib/PyYAML, the filesystem and `git` — no forge API, no CI-YAML
parsing, no `CI_*` variable it cannot run without — so a GitHub-hosted consumer
runs it unchanged from an Actions step. The `gitlab-only` rows above are the
porting bill; each carries a **Forge** note explaining what would have to be
replaced. (`check-versions.py` calls the GitHub *releases*
API as a version SOURCE; that says nothing about where the consumer is hosted.)

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
  `apt_url`/`apt_package`), optional `deploy_command`, `version_file`, `pin_regex`, `held`,
  `unreadable_current`,
  `notes`, `tag_filter`/`tag_regex`, `image_ref` (overrides the image name
  matched inside a `version_file` manifest when it differs from the API lookup
  name — a `ghcr.io/` prefix, Docker Hub's `library/` namespace), `source_url`
  (overrides the report link derived from the image name),
  `dockerhub_page_size` (tag page size, default 50, for image families whose
  suffixed tags do not fit one page), `apt_exclude_regex` (case-insensitive;
  matching versions are skipped) and `coupled_vars` (pins that must be rewritten
  with this one).
- **Env:** `GITHUB_TOKEN` / `GH_API_TOKEN` (optional; raises the GitHub rate
  limit from 60 to 5000 req/hr), `CHECK_VERSIONS_CONFIG` (config path,
  overridden by `--config`), `DEBUG` (any non-empty value also prints the full
  traceback for an unexpected error).
- **Two non-actionable states.** `held` is a deliberate hold; `unreadable_current`
  says the current version cannot be read here, so the entry reports the upstream
  release without flipping the exit code. Both are excluded from
  `summary.updates_available` and counted in their own JSON keys
  (`updates_held`, `updates_current_unreadable`).
- **Modes:** default report (exit 0 clean / 1 updates / 2 errors), `--json`,
  `--service`, `--category`, `--list`, `--update NAME`, `--update-all`,
  `--check-coverage` (fails when a `*_version` pin has no registry entry and is
  not in `untracked_allowlist`), `--no-cache`, `--clear-cache`, `--repo-root`.
  An entry declaring `coupled_vars` is written by `--update-all` and `--update`,
  with a `PAIRED-EDIT-REQUIRED` banner naming each partner the script cannot
  compute. A `--service` / `--category` combination that matches nothing prints
  one line on stderr and exits 2, not 1.
- **`--check-partner-pins BASE_REF`** reads the vars file at `BASE_REF` with
  `git show` and fails when an entry's `var_name` moved while one of its
  `coupled_vars` did not. Exit 0 clean, 1 on a stale partner, 2 when the base
  ref cannot be read. Run it as an MR gate beside `--check-coverage`.
- **Two partner pins the script writes itself**, unlike `coupled_vars`:
  `revision_var` is rewritten to `<new version>-r1` whenever the base version
  moves, and `sha_var`
  (`{"var": ..., "repo": <git url>, "ref": "refs/tags/v{version}"}`) is resolved
  with `git ls-remote`, taking the PEELED `^{}` line — a bare `refs/tags/<tag>`
  query returns the annotated tag object's sha, not the commit, and a build that
  verifies the tag then fails. A ref resolving to nothing aborts that service's
  write rather than storing an empty sha. Neither is reported by
  `--check-coverage` as an untracked pin.
- **`apt_repo` is the only apt fetcher.** `apt_url` (alias `apt_index_url`)
  takes one URL or a LIST tried in order until one yields the package, which is
  how a service published for several Debian suites is tracked;
  `apt_exclude_regex` drops pre-release stanzas. A response that is not a
  Packages index falls back to `<url>.gz`. There are no per-vendor categories.
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

**Registry entry fields**

| Field | Meaning |
|---|---|
| `name` | display name, unique |
| `var_name` | the pin's key in `vars_file`; `helm_chart_versions.<chart>` for a nested helm pin; any key for a `version_file` pin |
| `category` | `github` \| `dockerhub` \| `ghcr` \| `lsio` \| `helm` \| `apt_repo` \| `manual` |
| `deploy_command` | how to roll the bump out; falls back to `default_deploy_command` |
| `version_file` | the pin lives outside `vars_file`: a `version_file_aliases` key, a repo-relative path, or a list of paths that must agree |
| `held` | reported but never written (a documented upstream block); say why in `notes` |
| `unreadable_current` | the pin lives where this repo cannot read it: reported as `CURRENT UNREADABLE` with the upstream release, never actionable, never written; `notes` says where the pin is |
| `pin_regex` | (with `version_file`) matcher for a pin that is not an `image:` line — a CI variable, a `services: - name:` entry. The first capture group is the version; a regex with no group is an operator error |
| `tag_filter` | (github) regex the upstream tag must match |
| `tag_regex` | (dockerhub/ghcr/lsio) regex the image tag must match |
| `strip_prefix` | (github) drop `version_prefix` from the recorded version |
| `checksum_var` | `vars_file` key holding the artefact's sha256 (`sha256:<hex>` or bare hex); requires `checksum_url` |
| `checksum_url` | URL of the artefact to hash, `{version}` rendered from the entry's pinned version |
| `coupled_vars` | partner pins a human must rewrite with this one; the write prints `PAIRED-EDIT-REQUIRED` |
| `revision_var` | pin rewritten to `<new version>-r1` whenever the base version moves |
| `sha_var` | `{var, repo, ref}`; resolved with `git ls-remote`, taking the peeled `^{}` commit |

### `version-check-ci.py`

CI wrapper: runs the checker once with `--json`, prints a summary, writes
the report artifact (`--output`, default `version-report.json`; parent dirs
created), and posts an MR comment when there are actionable (non-held) updates
or errors. Exit code is derived from the report, not inherited from the
checker: 0 clean, 1 updates available, 2 errors or an unparseable report.

- **Env:** `CHECK_VERSIONS_CMD` (default `./scripts/check-versions.py`; it is
  expected to exit 0 up-to-date / 1 updates / 2 errors, and any other code is
  reported as 2), `CHECK_VERSIONS_LOCAL` (command named in the comment footer),
  `VERSION_CHECK_TIMEOUT` (default 600), `GITLAB_API_TOKEN` — the variable NAME
  is overridable with `--token-env`, so a consumer holding one bot PAT need not
  provision it twice.
- **The comment is refreshed, not re-posted.** The body carries a hidden
  `<!-- weisssrv:version-check -->` marker; each run finds its own note and PUTs
  it, so an MR pushed to N times keeps ONE Version Check note. A failed note
  listing degrades to a POST. Third-party text in the note (upstream error
  bodies) is quick-action-neutralised and fenced to its own backtick length.
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
  `CI_PROJECT_ID`, `CI_SERVER_HOST` + `CI_PROJECT_PATH`,
  `CI_DEFAULT_BRANCH` (default `--target-branch`), `CI_PIPELINE_URL`. The
  default push URL is credential-free
  (`https://gitlab-ci-token@$CI_SERVER_HOST/$CI_PROJECT_PATH.git`) and the token
  reaches git through `GIT_ASKPASS`, so it never appears in the runner's process
  table; a `--remote-url` is used verbatim and any credential in it is
  registered for log redaction.
- **Only tracked changes are committed** (`git add --update`), so report
  artifacts the check command drops stay untracked and out of the MR. Detection
  and staging share one list, read from `git status --porcelain -z` (raw,
  never-quoted paths) and staged with `:(top)`-anchored pathspecs, so a path
  holding non-ASCII characters and a `--repo-dir` below the repo root both work.
  The commit carries the same pathspecs as the staging, so anything the check
  command staged outside `--paths` stays out of it. Three staging rules explain
  that shape: `git add -- <pathspec>` exits 128 on a pathspec matching no
  TRACKED file (hence staging the DETECTED list, not the raw `--paths`),
  `git status --porcelain` C-quotes non-ASCII paths so `-z` is mandatory, and
  `git add` resolves pathspecs against the CWD.
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
`group` + `value: names|ips|ip|hostvar|groupvar` (optionally a single `host:`,
and `required: false`), plus `combine:` entries that union earlier keys in order.

```
generate-hosts-env.py --inventory <hosts.yml> --map <exports.yml>
    [--output <hosts.env>] [--group-vars <group_vars/>]
    [--regen-command "task hosts:sync"]
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
- **`value: hostvar`** with `var: <name>` emits `hostvars[<host>][<name>]` — a
  per-guest inventory value such as a Proxmox `vm_id`, which a consumer
  otherwise hardcodes in collectors and Taskfiles. With `host:` it resolves that
  one host; without, every host in the group. A missing `var:`, or a host not
  carrying the variable, fails as loudly as an absent host.

  ```yaml
  - key: PLEX_VMID
    group: plex_servers
    host: plex
    value: hostvar
    var: vm_id
  ```
- **`value: groupvar`** with `var: <name>` emits a GROUP-level variable, so a
  roster a role already owns (a pool list, a device list) is single-sourced into
  the env file instead of being retyped in a script. The value is read from the
  group's inventory `vars:` block, else from `group_vars/<group>.yml` or
  `group_vars/<group>/*.yml` under `--group-vars` (default: `group_vars/` beside
  the inventory); the inventory wins. A list space-joins. A missing `var:`, an
  undeclared group, a key the group does not declare, and an empty value all
  fail loudly, because a silently empty export reaches a script as an unset
  roster. `required: false` turns the group and variable lookups into an empty
  value, as it does for every other kind.

  ```yaml
  - key: ZFS_POOLS
    group: nas
    value: groupvar
    var: nas_storage_zfs_pools
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

The same flag enforces the VPA memory-cap rule, scoped to what each policy
controls: `maxAllowed.memory` **above** the container's limit fails whatever the
policy controls (the kubelet would reject the recommendation), and **equal to**
it fails only where the policy also controls limits (`controlledValues:
RequestsAndLimits` or unset) — there the updater rescales the limit with the
request, so the ceiling never binds. Under `RequestsOnly` cap == limit is the
correct shape. **Both arms are exempt when the policy is `Off`** — `updateMode:
Off` or a containerPolicy `mode: Off`: no recommendation is ever applied, so the
kubelet never sees the cap, and a finding there would be unfixable except by an
allowlist entry. Do not re-add it; flipping the VPA on brings the arm back. A VPA whose target workload is not rendered into this
kustomize-only corpus has no limit to compare against: it is reported as a cap
the gate could **not judge**, which fails the run unless its real limit is
declared in `vpa_cap_declared_limits` or the targets are acknowledged with
`--allow-unjudged-vpa-caps`. A silent skip is how a cap at double the declared
limit passes review.

The same flag flags a container whose `requests.memory` equals its
`limits.memory` while a VPA rewrites that limit (mode not `Off`, memory
controlled, `controlledValues` unset or `RequestsAndLimits`): the updater
preserves the ratio, so the limit tracks the request up forever and the
container never gains headroom. `controlledValues: RequestsOnly` is the correct
1:1 shape.

Without any flag it fails a container whose resource request exceeds its own
limit. The API server rejects that pod, so `kustomize build` and kubeconform
both pass the manifest and the Kustomization applying it never becomes ready.
Quantities are compared by value, so the suffixes may differ, and a value the
gate cannot parse is left unjudged rather than turned into a finding.

- **Config:** `--policy-config` with `chart_native_hpa_targets`
  (`namespace`/`kind`/`name`/`source`), `cpu_limit_allowlist`
  (`namespace/Kind/name`), `vpa_cap_allowlist`
  (`namespace/VerticalPodAutoscaler/name`, the grace list for caps not yet
  re-derived) and `memory_ratio_allowlist` (`namespace/Kind/name`). All
  optional; absent = empty. Each allowlist is a
  `{"namespace/Kind/name": "reason"}` mapping — one shape only, and a bare
  string entry is refused, because an unexplained exemption is a hole.
- **`vpa_cap_declared_limits`** supplies the memory limit of a target the corpus
  does not render (a chart-rendered DaemonSet, or one patched by a
  postRenderer), as
  `{"namespace/Kind/name": {container: limit}}`. It pins the target to its real
  limit rather than exempting it, so the cap is still compared; a rendered limit
  always wins over a declared one, and a value that is not a memory quantity is
  refused.
- **`--require-rendered-targets`** fails an HPA or VPA whose target workload
  the corpus does not render. Leave it off where charts render the targets, so
  an unrenderable target is reported rather than failed.
- **Exit codes:** 0 clean, 1 on a policy violation, 2 on an operator error (an
  unreadable or malformed `--policy-config`, an unparseable stdin corpus).
- **Example:** [`autoscaling-policy.example.yaml`](../examples/autoscaling-policy.example.yaml).
- Wire it as `extra_validation` for `ci/validate/flux-lint.yml`.

### `validate-helm-values.py` (PyYAML, needs `helm`; network)

`kustomize build | kubeconform` never renders a HelmRelease's chart, so
`.spec.values` is unvalidated. This extracts each listed release's values,
substitutes `${...}` from the cluster-versions ConfigMap, and runs
`helm template` (optionally piping to kubeconform). It reuses
`check-hpa-vpa-invariant.py`'s CPU-limit scanner so the kustomize-side and
chart-rendered-side policies cannot diverge, and requires that file beside it:
a missing sibling fails loudly. The VPA memory-cap rule is applied to the
chart-rendered pods too, reading the VerticalPodAutoscalers beside each release
manifest. A HelmRelease using `.spec.valuesFrom` or a `${var:=default}`-style
postBuild form is refused rather than rendered from half its values, and each
helm/kubeconform call is timeout-bounded (120s repo add, 300s render).

```
validate-helm-values.py [--kubeconform] [--repo-root DIR] [--releases FILE]
    [--versions-configmap PATH] [--policy-config PATH]
```

- **Releases file** (default `<repo-root>/helm-values-releases.yaml`): a list (or
  a `releases:` mapping) of `{name, manifest, chart}`. `repo_name` and
  `repo_url` are optional overrides: the chart repo resolves from the manifest's
  own `.spec.chart.spec.sourceRef.name`, matched against the `HelmRepository`
  documents under `--sources-dir` (default
  `kubernetes/infrastructure/sources`), the same way the chart version already
  does. When neither resolves, the run fails naming both fixes. A file under
  `--sources-dir` that will not parse as YAML fails the run naming that file,
  rather than vanishing from the resolution map.
- **`--crd-catalog-ref`** (default `$CRD_CATALOG_REF`, else `main`) sets the
  `datreeio/CRDs-catalog` ref kubeconform reads schemas from, so the family pins
  one sha in one place.
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
  but both failure messages name the resolved paths.
- **Exit codes:** 0 within skew, 1 on skew, 2 on an operator error (an
  unreadable input, or neither version found in the paths given).

### `extract-prometheus-config.py` + `lint-prometheus-config.sh` (PyYAML)

Extract alert rules from a HelmRelease's `additionalPrometheusRulesMap` and the
Alertmanager config from an ExternalSecret template into standalone files that
`promtool` / `amtool` can lint, then run them plus the promtool alert unit tests.

```
extract-prometheus-config.py rules <out> [--release PATH] [--rules-dir DIR]...
    [--require-release-rules] [--require-rules-dir]
extract-prometheus-config.py alertmanager <out> [--am-config PATH] [--dummy K=V]
```

- **`rules` is the union of two sources**: the HelmRelease's
  `additionalPrometheusRulesMap` and every `kind: PrometheusRule` document under
  each `--rules-dir` (default `kubernetes/infrastructure/observability/rules`).
  `--rules-dir` is repeatable, so a per-app PrometheusRule tree such as
  `kubernetes/apps` is linted alongside the shared rules tree. A rules dir that
  does not exist contributes nothing, so a repo with no standalone rules gets
  the HelmRelease alone. A group defined in both is caught
  by promtool's duplicate-name check.
- **`--require-release-rules`** asserts the HelmRelease declares inline rule
  groups. The chart ignores a mistyped `additionalPrometheusRulesMap` too, so
  without the flag those alerts are dropped silently; with it the gate reds. It
  is OFF by default: a consumer keeping every rule as a standalone
  PrometheusRule needs no flag, and one that relies on the inline map must pass
  it. `lint-prometheus-config.sh` forwards it from `REQUIRE_RELEASE_RULES`.
- **`--require-rules-dir`** asserts the standalone-PrometheusRule tree exists,
  exiting 2 and naming the missing directory. `lint-prometheus-config.sh`
  forwards it from `REQUIRE_RULES_DIR`.
- An empty, comment-only or explicitly-null manifest reports
  "not found in <path>" and exits 1 rather than raising.

`lint-prometheus-config.sh` env: `EXTRACT_SCRIPT`, `RULE_TESTS_DIR`,
`HELM_RELEASE`, `RULES_DIR`, `AM_CONFIG`, `EXTRACT_ARGS`,
`REQUIRE_RELEASE_RULES`, `REQUIRE_RULES_DIR`, `ALLOW_NO_RULE_TESTS`. An empty or absent `RULE_TESTS_DIR` FAILS the gate,
naming the resolved directory, so a dropped shard variable cannot green a run
that tested no alerts; set `ALLOW_NO_RULE_TESTS=1` to skip them on purpose.
`RULES_DIR` is whitespace-separated and
becomes one `--rules-dir` per tree. A `--rules-dir` given explicitly (or through
`RULES_DIR`) must exist; only the default path is allowed to be absent, and
`REQUIRE_RULES_DIR` makes it required too. `EXTRACT_ARGS` is whitespace-split
and appended to the rules invocation, for an extractor flag neither variable
covers.

### `check-flux-stage-timeouts.py` (PyYAML)

Fails a `wait: true` Flux Kustomization that does not outlast the slowest
HelmRelease under its `spec.path`. A waiting stage fails the moment its own
timeout expires, so a slow but healthy install reds the stage and every stage
that depends on it.

```
scripts/check-flux-stage-timeouts.py --repo-root . --cluster-dir kubernetes/clusters
```

- Stages are selected on `wait` alone. A waiting stage with **no explicit
  `spec.timeout`** is a violation, not a skip: it inherits the interval-derived
  default, which no reviewer sees in the manifest.
- Equal timeouts count as too tight — the stage has no headroom at all.
- `spec.timeout`, `spec.install.timeout` and `spec.upgrade.timeout` all count on
  the release side; the longest wins per release.
- An omitted `spec.path` resolves to the source root, as it does for Flux.
- **Exit codes:** 0 clean, 1 on a stage with no headroom or no explicit timeout,
  2 on an operator error (a `--cluster-dir` or `spec.path` that does not exist, a
  manifest that will not parse, a corpus with no waiting stage, or one where no
  stage path holds a HelmRelease with an explicit timeout).

### `check-flux-version-pin.py`

Holds the Flux version to one value across the three places it is written: the
`FLUX_VERSION` pin in CI, `flux_version` in the cluster-versions ConfigMap, and
the `# Flux Version:` header of every committed `gotk-components.yaml`.

```
scripts/check-flux-version-pin.py --repo-root .
```

- `--ci-file`, `--versions-configmap` and `--gotk-glob` retarget each source for
  another layout; the pin is matched as `FLUX_VERSION="x"` or `FLUX_VERSION: x`,
  so a shell assignment inside a block scalar and a YAML variable both read.
- The `# Components:` header must equal `--components`, which defaults to the
  set `flux bootstrap` installs. A cluster bootstrapped with
  `--components-extra` passes its own set, so a silent distribution change
  cannot pass as a version bump.
- `--runbook PATH` names the consumer doc quoted in the re-export remediation
  line, so the failure points at the repo's own procedure.
- A repository with no `gotk-components.yaml` yet (pre-bootstrap) still compares
  CI against the ConfigMap and says so.
- **Exit codes:** 0 agreement, 1 on a disagreement, 2 on an operator error (an
  unreadable subject, no pin, or no `flux_version` key).

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
  disagree. `FLUX_ENVSUBST_VARS` is the union too: a key defined in two
  ConfigMaps appears once.

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
  both errors. So is a multi-line `.data` value: the export block is re-read
  line by line, and a newline could not survive envsubst into YAML.
- **Dual-maintained:** weisssrv vendors this file and the flux-lint template
  takes the path from the consumer tree, so the vendored copy is the one CI runs.

### `kubeconform-skipped.py`

Reads `kubeconform -output json` on stdin and prints the distinct
`apiVersion/Kind` pairs kubeconform **skipped** — the CRs whose CRD schema is
absent from the catalog. flux-lint runs kubeconform with
`-ignore-missing-schemas`, so without this a new CRD-backed kind starts shipping
with zero schema validation and no signal at review time.

```
kubeconform ... -output json | scripts/kubeconform-skipped.py [BASELINE]
```

Without a baseline it lists the skipped pairs and exits 0 — informational, and
flux-lint still pipes it with `|| true`. Given a baseline file (one
`apiVersion/Kind` per line, `#` comments allowed) it exits 1 for any skipped
kind the baseline does not list, which is what turns the tracker into a floor
assertion; flux-lint passes it through its `expected_skipped_file` input and
drops the `|| true`. Unparseable or non-object input exits 1 rather than passing
silently. The per-Kustomization kubeconform passes remain the actual gate — this
makes the gap visible. Dual-maintained with weisssrv.

### `check-dashboards.py` (PyYAML)

Gates Grafana dashboard JSON before it reaches Grafana: each file parses,
carries no grafana.com `__inputs` block and no surviving `${DS_*}` placeholder,
every nested `datasource.uid` is allowlisted, and the directory's file set
matches its `configMapGenerator` `files:` entries in both directions, with the
sidecar label and the folder annotation reaching each entry. Both may come from
the kustomization's file-level `generatorOptions` or from an entry's own
`options`; the entry wins per key, as kustomize itself resolves them.

```
scripts/check-dashboards.py kubernetes/infrastructure/observability/dashboards
```

- `--allowed-uid` (repeatable, default `prometheus` `loki`); Grafana's built-ins
  (`grafana`, `-- Grafana --`, `-- Mixed --`, `-- Dashboard --`) and a `${var}`
  reference whose name the dashboard declares in `templating.list`; an
  undeclared variable is a finding.
- A legacy string `datasource` and a `datasource` object carrying no `uid` are
  findings too: provisioned Grafana matches by uid, so both resolve to whichever
  datasource is default.
- Only uids nested under a `datasource` key are checked, so a dashboard's own
  top-level `uid` stays free-form.
- `--dashboard-label`, `--dashboard-label-value` (default `1`) and
  `--folder-annotation` name the sidecar contract;
  `--skip-registration` lints a directory that holds dashboards as source rather
  than registering them with a configMapGenerator.
- **Exit codes:** 0 clean, 1 on a finding, 2 on an operator error including a
  run that scanned zero dashboards, or a directory named on the command line
  that does not exist. A stale hit from the default glob is still skipped.

### `check-helm-repo-parity.py` (PyYAML)

Holds every chart-repo URL equal to the HelmRepository CR Flux pulls from: each
helm-values `repo_name` must name one and carry its `spec.url`, and each
version-registry `helm_repo` must be a URL some HelmRepository declares.

```
scripts/check-helm-repo-parity.py
```

- `--sources-dir` (default `kubernetes/infrastructure/sources`), `--releases`,
  `--registry`, `--allow-empty`; a list that does not exist is skipped, but with
  neither present the run is an operator error.
- A releases file and a registry that exist but declare no repo at all is also
  an operator error, since nothing was compared. `--allow-empty` is the opt-out
  for a consumer that resolves every repo from its manifests.
- The success line reports the number of comparisons actually made.
- **Exit codes:** 0 clean, 1 on a mismatch, 2 on an operator error including a
  sources directory with no HelmRepository.

### `flux-child-kustomizations.py` (PyYAML)

Prints a cluster's child Flux Kustomizations in `dependsOn` order (topological,
ties alphabetical), so a reconcile loop or a deploy verifier never hand-lists
the stages. `--dir` defaults to the single directory under
`kubernetes/clusters/`; `--paths` prints `name<TAB>spec.path` instead of the
name alone, and `--exclude NAME` (repeatable) skips another Kustomization
besides `flux-system`. A
`dependsOn` cycle exits **2**: the ordering still prints, for diagnosis, but it
does not satisfy the declared dependencies. Exit 1 when the directory holds no
Kustomization. `--paths` exits 1 and names the offenders on
stderr when any Kustomization declares no `spec.path`, so a missing stage cannot
pass as a complete corpus. `--allow-missing-paths` prints the paths there are and
still warns. `--require-paths` is accepted as a no-op alias for the default.

### `run-render-gates.sh`

Runs a consumer's ordered corpus-gate list from `scripts/render-gates.conf`
(`--config`) instead of a one-line `extra_validation` string.
`Label :: command` runs with `$RENDER_ALL` on stdin; `Label ::! command` runs
without the redirect, for a step that appends to the corpus or reads other
inputs. `--flux-env <script>` builds one merged versions ConfigMap up front,
exports `$MERGED_CM` and removes it on exit.

Config lines are eval'd, so the file is repo-owned code. Exit 0/1; exit 2 for an
unset or empty `RENDER_ALL`, a missing config, an empty gate list, a line
with no command, or a gate that itself exits 2 or more (an operator error is
propagated, never collapsed into a finding).

- **Example:** [`render-gates.example.conf`](../examples/render-gates.example.conf).
  It leaves every gate's vacuity guard armed; `--allow-empty` is a per-consumer
  opt-out, not a default.

---

## Cluster invariant gates

Fifteen gates plus one shared module, in two invocation shapes. **Eight read
the rendered manifest corpus on stdin through `gate_common.py`** —
`check-pvc-storageclass.py`, `check-scrape-netpol.py`,
`check-default-deny-coverage.py`, `check-issuer-refs.py`,
`check-secretstore-scope.py`, `check-nfs-tls.py`,
`check-ephemeral-storage-cap.py` and `check-ingressroute-backends.py` — where
the corpus is what
`task flux:lint` accumulates from `kustomize build | envsubst`, so they wire
into `ci/validate/flux-lint.yml`'s `extra_validation` chain. **The rest take
paths or flags**: `check-netpol-except-parity.py` (manifest paths, plus an
optional rendered stream via `--corpus`), `check-kustomization.py`
and `check-scrape-wiring.py` (manifest paths), and
`check-alertmanager-behaviour.py`, `check-backup-artifact-apps.py`,
`check-cluster-invariants.py` and `check-role-inputs.py` (flags). Where a gate
needs site data it comes from a flag or a config file, never from the source, so
the shipped file is identical in every consumer — `check-pvc-storageclass` needs
none at all, and `check-secretstore-scope` only an optional `--external-store`.

### `gate_common.py` (PyYAML, not run directly)

The helpers the corpus gates share: the stdin loader (`load_docs`, flattening
`kind: List` and bare top-level lists, raising `OperatorError`), the
empty-corpus message, `doc_namespace` / `doc_key` (an omitted namespace IS
`default`, and `namespace/Kind/name` is the one spelling every allowlist uses),
and the NetworkPolicy rules — `policy_types` (absent is inferred, `[]` restricts
nothing), `selects_all_pods`, `peer_selects_everything`, `zero_prefix` and
`excepts_cover_cidr`. `peer_selects_everything` reads a peer with an empty
`podSelector` and no `namespaceSelector` as narrowing, because an omitted
`namespaceSelector` scopes the peer to the policy's own namespace.

`load_live_items(stream, what=...)` is the same fail-closed arm for a live
`kubectl get -o json` payload: it parses stdin, rejects a payload whose `items`
is not a list, and **exits 2 on a valid but empty item list**. The wrong context
or selector returns one, so a cluster-drift gate that passed on it would read as
a clean cluster. Any gate reading live input uses this rather than its own guard.

`load_cluster_config(root, path=CLUSTER_CONFIG)` reads the cluster-config
ConfigMap's `data:` map, every value coerced to a string, and raises
`OperatorError` on an unreadable file or an empty map. `substitute(value,
config)` resolves a whole-value `${name}` placeholder the way Flux's postBuild
does; an unknown name and any partial spelling come back untouched, to fail the
caller's own parse. Gates that reach for a cluster-identity value use these
instead of each re-implementing the reader, and wrap `OperatorError` in their
own failure type.

- **Vendoring:** every corpus gate below that imports it does so from its own
  directory. Vendor it wherever any of them is vendored.
- Not invoked directly and takes no flags.

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
- **CRITICAL: a static bind cannot be grown — replace it, do not resize it.**
  `storageClassName: ""` satisfies this gate, but the PersistentVolumeClaimResize
  admission plugin rejects any capacity increase on a claim that was not
  dynamically provisioned ("only dynamically provisioned pvc can be resized"), as
  does a class without `allowVolumeExpansion`. Flux reports the rejection against
  the whole Kustomization, so one edited size stalls every object in that stage.
  Resize the backing volume and the PV, then re-create the claim. This gate reads
  one rendered corpus and cannot see the capacity a claim carried before, so the
  base-versus-overlay diff stays the reviewer's job.
- **An empty corpus is an operator error, exit 2**, as it is for the two sibling
  stdin gates. This one takes no arguments at all, so a mis-piped invocation has
  no other symptom. **So is a corpus that arrived but declares no claim** — that
  is what a render loop which never reached the storage-declaring stages
  produces; the success line prints the claim count next to the document count.
- **Exit codes:** 0 clean, 1 on an unpinned claim, 2 on an operator error (an
  empty or claim-less corpus, unparseable input).
- No configuration: the rule is universal.

### `check-ephemeral-storage-cap.py` (PyYAML)

Fails a container that mounts a `sizeLimit` emptyDir without an
`ephemeral-storage` request AND limit, or whose limit is below the emptyDirs it
mounts.

```
cat rendered-corpus.yaml | scripts/check-ephemeral-storage-cap.py
```

The kubelet evicts on the CONTAINER's limit, not on the volume's `sizeLimit`, so
a limit below the sized volume kills the pod before the volume it sized ever
fills — and nothing in the manifest looks wrong. Writing the two numbers near
each other and asserting the relationship in a comment is what this replaces.

- **Subjects:** `Pod` and the pod template of `Deployment`, `StatefulSet`,
  `DaemonSet`, `Job`, `CronJob`, `ReplicaSet` and `ReplicationController`;
  `initContainers` as well as `containers`.
- Every sized emptyDir a container mounts draws on the same budget, so the gate
  weighs the limit against their SUM. Equal values pass: setting the limit to
  exactly the volume size is a deliberate shape.
- Quantities compare numerically, so `1Gi` against `512Mi` is caught. A quantity
  the gate cannot read is reported rather than assumed to be fine.
- A `medium: Memory` emptyDir is tmpfs, charged to the container's memory
  limit, so it needs no ephemeral-storage pair and is left out.
- **Exit codes:** 0 clean, 1 on a violation, **2** on an operator error — an
  empty or unparseable corpus, or one declaring no pod spec at all, which is
  what a render loop that never reached the workload stages produces.
- No configuration: the rule is universal.

### `check-ingressroute-backends.py` (PyYAML)

Fails a Traefik route backend or a `ServiceMonitor` endpoint whose Service or
port does not exist in the rendered corpus.

```
cat rendered-corpus.yaml | scripts/check-ingressroute-backends.py \
    [--allow-backend NAMESPACE/NAME=REASON]...
```

Both drifts pass `kustomize build` and kubeconform: a renamed Service leaves the
route resolving to nothing, which Traefik answers as a 503, and a
`ServiceMonitor` endpoint naming a port the Service does not declare leaves the
target silently unscraped. `check-scrape-netpol.py` covers the NetworkPolicy
half of that scrape path, not the port itself.

- **Route backends:** `IngressRoute`, `IngressRouteTCP` and `IngressRouteUDP`.
  A `kind: TraefikService` entry is Traefik's own weighted object, not a
  Service, and is not resolved. `port` matches either the Service port NUMBER or
  its name.
- **ServiceMonitor endpoints:** `endpoints[].port` is a port NAME, so a Service
  with an unnamed port cannot satisfy one. The Service set comes from
  `spec.selector.matchLabels` and `spec.namespaceSelector`; a monitor selecting
  only `matchExpressions` is not resolved, because that needs the API.
  A `targetPort` endpoint addresses the pod directly and is not a name
  reference.
- **A reference the corpus cannot resolve either way is skipped**, not failed: a
  backend in a namespace the corpus renders no Service for belongs to another
  repo or another render path. The success line reports how many references
  resolved, so a corpus that skipped everything is visible.
- **Exit codes:** 0 clean, 1 on a dangling reference, **2** on an operator error
  — an empty or unparseable corpus, or one where nothing resolved, which means
  the render paths do not cover the routes, the monitors and their Services
  together.
- **`--allow-backend NAMESPACE/NAME=REASON`** exempts a backend Service another
  repo or render path ships. The reason is mandatory and printed.
- No config file: the rule is universal, and the only knob is the flag above.

### `check-issuer-refs.py` (PyYAML)

Fails a cert-manager issuer reference that names no `ClusterIssuer` or `Issuer`
the corpus ships. A typo in an issuer name is accepted by the API server: the
Certificate sits Pending with no Secret, the Ingress serves the ingress
controller's own self-signed certificate, and nothing is logged where an
operator looks.

```
cat rendered-corpus.yaml | scripts/check-issuer-refs.py \
    [--allow-external NAME=REASON]...
```

- **What counts as a reference:** `spec.issuerRef` on any document, plus the
  ingress-shim annotations `cert-manager.io/cluster-issuer` and
  `cert-manager.io/issuer`. A reference whose `group` (or
  `cert-manager.io/issuer-group`) names another issuer implementation is left
  out, since its object is not a cert-manager Issuer.
- **An omitted `issuerRef.kind` means `Issuer`, not `ClusterIssuer`** — that is
  cert-manager's default, and a namespaced Issuer must live in the referring
  object's own namespace, so the gate resolves it there.
- **`--allow-external NAME=REASON`** exempts an issuer a chart or another
  pipeline installs outside this corpus. The reason is mandatory and printed.
- **A corpus that declares no issuer reference is an operator error, exit 2**:
  that is what a render covering none of the TLS-declaring stages produces.
- **Exit codes:** 0 clean, 1 on an unresolved reference, 2 on an operator error
  (an empty or reference-less corpus, an exemption with no reason).

### `check-runbook-anchors.py`

Resolves every alert `runbook_url` annotation under the observability tree
against the docs tree and fails on a doc that does not exist or an anchor no
heading slugs to. Stdlib only, no network.

```
scripts/check-runbook-anchors.py \
  --rules-dir kubernetes/infrastructure/observability --docs-dir docs
```

- Scans the WHOLE `--rules-dir` tree, `*.yaml` and `*.yml`. A Loki ruler's rule
  files sit beside its chart values rather than under a `rules/` subdirectory,
  so a gate narrowed to `rules/` leaves those annotations unchecked.
- An in-repo `runbook_url` must start with `--base-placeholder` (default
  `${cluster_runbook_base_url}/`), the Flux substitution variable the rendered
  link is built from. A literal path is reported: it would not survive
  substitution. `http://` and `https://` targets are skipped.
- Anchors follow GitHub's slug rules: inline markup dropped, lowercased, spaces
  to hyphens, and a repeated heading takes a `-1`, `-2` suffix. Headings inside
  a fenced block are not anchors.
- **Section pointers in alert text:** a `(docs/06 § NAS memory)` pointer in an
  alert's `description` or `summary` is resolved against that document's real
  headings, and a missing one is reported with the alert's own name. The
  parentheses bound the section name, so the prose around it is never read as
  part of the heading, and a pointer whose document cannot be pinned down is
  left alone.
- **Exit codes:** 0 clean, 1 dangling annotations or section pointers, 2 operator error — a missing
  `--rules-dir` or `--docs-dir`, or a tree carrying no `runbook_url` at all. A
  gate that checks nothing is not a gate, so an empty scan fails rather than
  passing.
- **Vendoring:** offered in `scripts/vendorable-paths.yml`. Vendor
  `tests/test_check_runbook_anchors.py` with it to keep the negative cases.

### `check-scrape-netpol.py` (PyYAML)

Fails a namespace that is scraped AND ingress-restricted but admits no traffic
from the observability namespace, and a monitor whose endpoint port is declared
by nothing it selects. Kubelet probes bypass the CNI policy chain, so
the pod stays healthy while the scrape is REJECTed and the only symptom is
`TargetDown`.

```
cat rendered-corpus.yaml | scripts/check-scrape-netpol.py \
    [--observability-namespace NS] [--exempt NS=REASON ...]
    [--prometheus-pod-label KEY=VALUE ...]
```

- A namespace counts as scraped from a ServiceMonitor/PodMonitor (its own
  namespace, or `spec.namespaceSelector.matchNames`) **or** from a HelmRelease
  whose values enable a chart-native monitor — the case the kustomize corpus
  cannot otherwise see. `namespaceSelector.any` is unattributable and skipped.
- **`--exempt` requires a reason** (`NS=REASON`); an unexplained exemption is
  rejected at parse time, and an exemption no namespace exercised is reported on
  the success path, never fatal.
- **`--prometheus-pod-label KEY=VALUE`** (repeatable) names a label the scraper
  pods carry. Declaring them lets a peer that narrows the observability
  namespace with a `podSelector` be judged; without them such a peer is
  credited unexamined.
- A document with no `metadata.namespace` is read as the API reads it: it is in
  `default`, matching the sibling gate. `policyTypes: []` restricts nothing.
- **A monitor's endpoint port must resolve.** For each
  ServiceMonitor/PodMonitor whose `spec.selector` is plain `matchLabels`, the
  gate resolves `endpoints[].port` (`podMetricsEndpoints[].port` for a
  PodMonitor) through the Services, Deployments, StatefulSets and DaemonSets
  that selector matches in the target namespace, and fails when none declares a
  port of that name. A renamed port leaves prometheus-operator with zero
  targets, so no `up` series ever appears and an `up == 0` alert arm has nothing
  to fire on; such an alert needs an `absent()` arm too. A selector the corpus
  matches nothing for is skipped, since a chart may render the Service, and a
  `matchExpressions` selector is not modelled.
- **Namespace-level reachability otherwise, so the pass is not proof the scrape
  lands.** The policy's own targeting is not checked: neither the port it opens
  nor its `spec.podSelector`, because the pods a monitor selects are unresolved
  here. A namespace whose only observability allow is attached to some OTHER
  workload — or admits a port the exporter does not listen on — passes this gate
  with the scrape still REJECTed. The gate catches the whole-namespace
  omission, which is the failure that actually recurs; a `TargetDown` that
  survives a clean run is the signal to check the live policy's selector.
- **The port-level half is its own gate.** `check-scrape-wiring.py` below
  checks the allow policy's own `podSelector` and port against the pods a
  monitor selects. It reads a single-namespace manifest tree, where the
  monitor, the workload and the policy resolve against each other; this gate
  spans every namespace in a cluster corpus and cannot assume that.
- **An empty corpus is an operator error, exit 2**, as it is for the two sibling
  stdin gates. **So is a corpus that arrived but holds no scrape target** — the
  observability stage never rendered, so every namespace went unexamined. Scrape
  targets with none ingress-restricted among them is still a pass (default-deny
  is a per-namespace choice); the success line prints both counts.
- **Exit codes:** 0 clean, 1 on a blocked scrape, 2 on an operator error (an
  empty or target-less corpus, unparseable input, a malformed `--exempt`).

### `check-scrape-wiring.py` (PyYAML)

Fails a monitor whose scraped PORT no NetworkPolicy admits from the
observability namespace. Reads a manifest tree from **paths** and judges it one
effective namespace at a time, so the monitor, the Service, the workload and the
policy resolve against each other. `check-scrape-netpol.py` is the
namespace-granularity gate for a whole cluster corpus; this one is the port
granularity.

```
scripts/check-scrape-wiring.py [--observability-namespace NS]
    [--namespace NS | --namespace-from-tree] [DIRECTORY]
```

- A ServiceMonitor resolves through **every** Service its labels select, then
  that Service's `targetPort` (or its `port`) to the container port, by name or
  by number. A PodMonitor's `port` / `portNumber` / `targetPort` resolves
  straight against `containerPorts`, with no Service hop.
- A policy is credited when its `podSelector` matches the target pods and an
  ingress rule admits the observability namespace on that port. An absent or
  **empty** `ports:` list matches every port, as the API reads it, and
  `endPort` is read as a range.
- A shape the gate does not model — `matchExpressions`, an `ipBlock` peer, a
  peer scoping the namespace with a `podSelector` — is **not credited**, and is
  named in the failure so the reader can tell "wired wrong" from "not modelled".
- A matched Service with **no `spec.selector`** is a Violation, not a target:
  its endpoints are managed by hand, so no workload in the tree serves the
  scraped port and crediting a policy against it certifies nothing.
- **One port name resolves to one number.** A port name two selected workloads
  declare at different numbers is an operator error: a policy naming it would be
  credited against whichever workload was read last.
- **Documents are grouped by effective namespace** — their own
  `metadata.namespace`, else `--namespace` — and each group is judged against
  its own policies, so a policy in one namespace never admits a scrape in
  another. A tree stating exactly ONE namespace lends it to the documents that
  name none, the way its Kustomization does; stating several while a document
  names none is an operator error, and so is a stated namespace that disagrees
  with `--namespace`.
- A monitor whose `spec.namespaceSelector` reaches outside its group is an
  operator error: the policies there cover one namespace, so a wider scrape must
  be checked where those policies live. A `matchNames` entry is verified
  against the group's namespace (`--namespace`, or the one the document states)
  and refused without one, so a stale name never certifies against the wrong
  policies.
- **`--namespace-from-tree`** reads that namespace from the one Namespace
  manifest under the directory, for a pipeline vendored byte-identically that
  cannot carry a tenant's value (`ci/github/ci.example.yml` passes it). It is
  consulted only when a monitor declares `matchNames`, so a tree whose
  Namespace the cluster operator owns is unaffected; when one is declared and
  the tree names no namespace, or names several, that is the operator error.
  Mutually exclusive with `--namespace`.
- **Exit codes:** 0 clean, 1 on an unadmitted port or a selectorless Service, 2
  on an operator error — a directory that does not exist, a manifest that does
  not parse, a corpus with no kinded document, a monitor with no endpoints or a
  selector matching every pod, an ambiguous namespace grouping, one port name at
  two numbers, and a policy that admits the observability namespace while no
  monitor is present in its namespace at all.

### `check-kustomization.py` (PyYAML)

Fails a manifest that is present but no `kustomization.yaml` lists, a listed
path that does not exist, and a list that renders nothing. Reads the tree from
**paths** and recurses into every directory it lists.

```
scripts/check-kustomization.py [DIRECTORY]
```

- Both directions. An unlisted manifest is inert, which `kustomize build` and
  kubeconform both pass; an emptied or mistyped `resources:` list renders
  nothing, and a cluster-side Kustomization with `prune: true` then deletes
  every object this repo applied.
- Listed paths are read from every content key, not only `resources:` —
  `bases`, `components`, `patches`, `patchesStrategicMerge`, `patchesJson6902`,
  `replacements`, `configMapGenerator`, `secretGenerator`, `helmCharts` and
  `openapi`. A `kind: Component` legally carries patches only, so it is held to
  contributing through one of those keys rather than to listing resources.
- A remote base is fetched by kustomize, so it is not looked for on disk: an
  entry with a scheme, a `host/org/repo//path` shape, a `.git` component or a
  `?ref=` query is skipped. A listed directory covers the manifests beneath it.
- **Exit codes:** 0 clean, 1 on a violation, 2 on an operator error — a
  directory that does not exist, a root with no `kustomization.yaml` at all (a
  run that inspected nothing is not a gate), and a `kustomization.yaml` that is
  unreadable, unparseable or not a mapping.

### `check-default-deny-coverage.py` (PyYAML)

Fails a namespace that owns a workload but carries no namespace-wide ingress
default-deny. This is the half its sibling `check-scrape-netpol.py` structurally
cannot see: that gate only inspects namespaces which ALREADY run an ingress-deny
policy, so a namespace with no policy at all is invisible to it rather than a
finding.

```
cat rendered-corpus.yaml | scripts/check-default-deny-coverage.py \
    [--exempt NS=REASON ...] [--require-egress NS ...]
```

- **`--require-egress NS`** (repeatable) also demands a namespace-wide egress
  default-deny in that namespace. The ingress mandate is cluster-wide; egress is
  opt-in per namespace, because an egress fence needs its own allow set.
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
  `namespaceSelector: {}` — an EMPTY label selector matches every object in
  its scope — or a `/0` `ipBlock` whose `except` list leaves
  any address admitted, judged by exact subtraction rather than by assuming
  which ranges a cluster's pods occupy). A peer with an empty `podSelector` and
  no `namespaceSelector` is scoped to the policy's own namespace, so it narrows
  and still fences. Peers within a rule are OR'd, so one wide peer
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

### `check-helmrelease-crd-safety.py` (PyYAML)

Fails a CRD-owning HelmRelease that would take its CustomResourceDefinitions —
and every CR of them — with it on an uninstall. A helm uninstall cascade-deletes
the CRDs a chart owns, and install remediation defaults to uninstalling.

```
cat rendered-corpus.yaml | scripts/check-helmrelease-crd-safety.py \
  --policy-config kubernetes/helmrelease-crd-safety.yaml
```

- `--policy-config` is required: whether a chart ships CRDs cannot be read off
  the HelmRelease. `examples/helmrelease-crd-safety.example.yaml` is the
  template.
- **Every HelmRelease in the corpus belongs in `crd_keepers` or `no_crds`.** An
  undeclared one fails, so a chart that starts shipping CRDs cannot arrive
  unnoticed; a `no_crds` entry carries the reason structurally.
- `crd_keepers` names the **shape** that keeps the CRDs — `values`,
  `postRenderer` or `keep` — so a values key the chart never reads fails instead
  of passing on the string alone. The first two also require
  `spec.install.strategy.name: RetryOnFailure`; the `keep` shape survives an
  uninstall by itself.
- `required_extra_args` pins argv entries a controller's own default would
  silently stop matching, such as an external-dns annotation prefix.
- A declared key absent from the corpus fails: its guard would check nothing.
- **Exit codes:** 0 clean, 1 on a violation, 2 on an operator error (an unusable
  policy file, an empty corpus, or a corpus with no HelmRelease).

### `check-secretstore-scope.py` (PyYAML)

Fails an unscoped `ClusterSecretStore` — referenceable from every namespace, so
any ExternalSecret in the cluster can read the whole backing vault — and any
ExternalSecret, or namespace a ClusterExternalSecret fans out to, sitting in a
namespace its conditions do not admit. A store is unscoped with no
`spec.conditions` **and** when any condition admits every namespace (an empty
`namespaceSelector`, or a catch-all `namespaceRegexes`).

```
cat rendered-corpus.yaml | scripts/check-secretstore-scope.py
```

- Condition matching mirrors ESO: a namespace is admitted when ANY condition
  matches, on an exact `namespaces` entry, a `namespaceRegexes` match, or a
  `namespaceSelector` label match. A ClusterExternalSecret's
  `namespaceSelector: {}` is a selector with no terms and therefore matches
  EVERY namespace — absent and empty are not the same thing.
- A `namespaceSelector` carrying a key outside `matchLabels` / `matchExpressions`
  — a singular `matchLabel:`, or an extra sibling — is reported as **unmodelled**,
  not as a non-match: the CRD prunes the unknown key, so the apiserver keeps the
  empty selector and the condition (or fan-out) reaches every namespace. The
  store's consumer admissions are skipped once it is reported, because they
  certify nothing.
- A ClusterExternalSecret's fan-out is the **union** of `spec.namespaceSelectors`
  (or the deprecated singular `spec.namespaceSelector`) and its literal
  `spec.namespaces` list, the way ESO resolves it — a CES written with the list
  alone matches no selector.
- **A store referenced but not defined in the corpus FAILS.** That is the runtime
  failure the gate exists to catch: the ExternalSecret never syncs and the Secret
  goes stale. `--external-store NAME` (repeatable) declares a store genuinely
  managed outside the linted tree, so the exemption is visible. A
  `--external-store` name nothing referenced is reported on the success path.
- **An empty corpus is an operator error, exit 2** — a broken pipe or a wrong
  `kustomize build` path must not report green. So is a corpus that HAS documents
  but holds neither a ClusterSecretStore nor a consumer: that is what a render
  loop which never reached the defining stage produces, and it is the likelier of
  the two wiring failures.
- **Exit codes:** 0 clean, 1 on a scoping violation, 2 on an operator error (an
  empty or store-less corpus, unparseable input, or a `namespaceRegexes` entry
  that does not compile — a defect in its own right, and the fail-open direction
  for the catch-all probe).
- No configuration file: the rule is universal; `--external-store` is the only
  site-shaped flag.

### `check-netpol-except-parity.py` (PyYAML)

Reads NetworkPolicy manifests from **paths**, and optionally a rendered stream
via `--corpus`, and asserts no fenced pod has unrestricted egress, three ways:
every egress `ipBlock` /0 peer carries one of the canonical reserved-CIDR
except-lists exactly and in order; no egress rule reaches a whole fenced range
(a /0 written as two /1s, or a lone `192.168.0.0/16`, are the same escape); and
an egress rule that allows every destination is declared with a reason.

```
scripts/check-netpol-except-parity.py [--config FILE] [--corpus FILE] [path ...]
```

- **Config keys:** `canonical_except_lists` (name -> `[cidr]`, replaces the
  built-in `reserved-full` / `lan-fence` sets **wholesale** — declare both, or
  omit the key), `fence_networks` (the ranges no rule may reach in full;
  defaults to the v4 LAN fence plus `fc00::/7` and `fe80::/10`),
  `unrestricted_egress_ok` (`"<namespace>/<name>"` -> reason).
- **Without `--config` the allowlist is EMPTY**, so a peer-less egress rule
  fails until it is declared. An entry with a blank reason is rejected.
- `--config` yields a `Policy` value (`load_config` returns it;
  `classify` / `unfenced_reach` / `scan_paths` / `check_paths` take an optional
  `policy=`), so an importing caller never mutates module state. An unparseable
  ipBlock CIDR, or a malformed egress/ingress rule, is an exit-2 operator error
  naming the file — not a fence finding.
- Ingress is exempt, whatever it excludes: an unfenced `0.0.0.0/0` ingress
  peer is a deliberate shape (a WAN endpoint). A narrower egress block keeps
  its own except-list too; the canonical lists are the egress /0 contract.
- **Which corpus it judges.** A path scan reads the tree **as written**, so a
  `${name}` substitution placeholder in a CIDR is left unevaluated, neither
  parsed nor reported: a template spells site ranges that way and the consumer
  substitutes them before the API sees the manifest. The consequence is that in
  a placeholder-shaped repo the LAN-escape arm examines nothing, so a consumer
  pipes its substituted render through `--corpus FILE` (`-` for stdin), where
  the skip is off: the fence arms judge real CIDRs and a leftover `${...}` is an
  exit-2 operator error. `--corpus` replaces the default `kubernetes/` tree, not
  an explicit path list — pass both to scan both.
- **A `.json` manifest is scanned too** — the directory walk globs `*.yaml`,
  `*.yml` and `*.json`, and a JSON file holds one document or a top-level list
  of them. An unparseable one is an exit-2 operator error like its YAML sibling.
- **The empty peer is the allow-everything case.** `to: [{}]` is a non-empty
  peer list carrying no constraint, and Kubernetes reads a peer with none of
  `ipBlock` / `podSelector` / `namespaceSelector` as every destination — the
  same finding as a rule with no `to:` at all, and declared the same way.
  `podSelector: {}` is a real peer (every pod in the namespace), not the empty
  one.
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
redundant `equal:` label (which makes the pair dedup nothing), alertnames that
no longer exist, and a warning/critical escalation pair with no inhibit rule.

```
scripts/check-alertmanager-behaviour.py --config FILE [--repo-root DIR]
                                        [--extract-script PATH]
                                        [--extract-arg FLAG]
```

- Extracts the config and rules through the consumer's
  `extract-prometheus-config.py` (default `<repo-root>/scripts/`), so a consumer
  that forked the extractor keeps its own. `--repo-root` is the extractor's
  **cwd** as well as where it is looked up — the extractor resolves its manifest
  defaults relative to the process cwd, so the gate runs from anywhere. Both
  paths are resolved ONCE against the caller's cwd, so a relative `--repo-root`
  is not re-resolved by the child (which would double its prefix), and a
  `--repo-root` that is not a directory exits 2 naming the flag.
- `--extract-arg FLAG` (repeatable) is passed through to the extractor, for a
  consumer that needs its own `--rules-dir` or one of the extractor's
  `--require-*` assertions.
- The resolved receiver is compared **exactly**, against the first token of
  amtool's output (it can print several matching receivers in tree order). A
  prefix comparison would pass `critical-page` for an expected `critical`.
- **Config keys:** `route_cases` (required, non-empty; each `receiver` +
  `labels`), `synthetic_route_alerts` (route-case alertnames that deliberately
  name no rule), `upstream_alerts` (alertnames shipped by a chart's own rule
  groups, invisible to the extractor), plus the escalation keys below.
- **`matcher_parity_labels`** turns on the matcher-value arm, opt-in per label.
  For each listed label, an inhibit rule's `=~`/`!~` matcher on it must be a
  selector value the mirrored rule's own `expr` declares, compared exactly. It
  catches an inhibit rule written to reproduce a rule's exclusion set drifting
  when that rule gains or loses a member. An exact `=` matcher pins one series
  and claims to mirror nothing, so it is left alone.
- **Every escalation pair must be inhibited.** A `severity=warning` alert whose
  name plus an `escalation_suffixes` entry (default `Prolonged`, `Critical`) is a
  `severity=critical` alert in the same corpus is a pair, and a pair with no
  inhibit rule pointing from the critical to the warning pages twice for as long
  as the failure lasts. `escalation_equal` lists label keys that rule must share,
  so one instance's critical does not silence another instance's warning. One
  matching rule has to carry every key on its own: two rules each missing a
  different key do not add up, and the finding names the closest rule and the
  keys it lacks. `escalation_pairs` declares a pair whose names share no stem,
  and `escalation_exceptions` declares one that delivers both severities on
  purpose. Each entry is a `critical` + `warning` mapping, with an optional
  per-pair `equal` list.
- **Every member of a regex alternation is checked**, not just "at least one
  survives", and a regex that is not a plain alternation is REPORTED rather than
  skipped — an empty name set would otherwise pass silently.
- **A label may carry several matchers and every one is read.** Alertmanager ANDs
  them, so a positive regex narrowed by a second negative matcher on the same
  label is honoured whichever order the two are written in. That second negative
  matcher is also the remediation the target-scope finding asks for.
- **What a rule covers is the conjunction, not its positive names.** A negated
  `alertname` subtracts from the positive set, so a rule that excludes an alert
  never certifies an escalation pair naming it, is not held to that alert's own
  expr, and is not read as that alert's source scope. A negated regex the gate
  cannot read leaves the covered set unknown, which certifies nothing either; the
  unreadable regex is itself reported.
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
scripts/check-backup-artifact-apps.py --host-vars FILE --rules FILE [--allow-empty]
```

- Both paths are site data, so both flags are required.
- Nothing declared on either side is an operator error: the gate paired nothing
  and would certify a contract it never inspected. `--allow-empty` is the
  opt-out for a consumer that collects no backup artefacts; it also accepts a
  rules corpus that defines no `BackupArtifactStale` rule at all, which is the
  state such a consumer is actually in. Declared apps with no alert still fail.
- A `BackupArtifactStale` rule that exists with no `absent()` arm fails even
  under `--allow-empty`. The flag covers the consumer that ships no such rule,
  not a rule that guards nothing, so the two states are told apart rather than
  both reading as an empty arm set.
- The rule is read as TEXT, scoped to the alert's own block: it lives inside a
  HelmRelease `values:` blob several levels deep, carrying Go-template
  `{{ $labels }}` strings, so a structural walk buys nothing.
- **Exit codes:** 0 in sync, 1 on drift, 2 when either file is missing, the
  pairing is empty without `--allow-empty`, or the rule is present with no arm.

### `check-nfs-tls.py` (PyYAML)

Fails an NFS PersistentVolume that does not mount over TLS by hostname. A
TLS-only export rejects a plaintext mount, so the PV does not degrade — it fails
to mount after the pod is scheduled; and a wildcard certificate has no IP SAN,
so a PV naming the server by IP fails the handshake.

```
cat rendered-corpus.yaml | scripts/check-nfs-tls.py --cert-domain example.com
```

- **Scope: `spec.nfs` on a PersistentVolume only.** A pod-inline
  `volumes[].nfs` has no `mountOptions` field at all, and a CSI-provisioned
  volume keeps its server and options in the StorageClass parameters, so
  neither is covered and both would need a different check.
- `--required-option` (default `xprtsec=tls`), `--cert-domain` (message text
  only), `--allow-empty` for a cluster with no NFS storage. The shipped example
  config leaves the guard armed.
- **`--allow-ip-server`** is for a consumer whose export is reached by IP: it
  declares that the server certificate carries an IP SAN. It trades the
  handshake arm away, so a wildcard certificate with no IP SAN still fails the
  mount at runtime with the gate green.
- Loads the corpus through `gate_common.py`, so a PV inside a `kind: List` is
  inspected. Vendor `gate_common.py` alongside it.
- **Exit codes:** 0 clean, 1 on a finding, 2 on an operator error — an empty
  corpus, or a corpus with no NFS PV unless `--allow-empty`.

### `check-cluster-invariants.py` (PyYAML)

Inventory invariants that otherwise surface phases after the edit that caused
them: a duplicate `vmid` (pct and qm share one namespace), two hosts on one
`ansible_host`, a host configured on a cluster VIP, a host outside the LAN CIDR,
and an alert rule pinning an address no host holds. The VIPs and the CIDR come
from the cluster-config ConfigMap, so the inventory is checked against the same
identity the manifests substitute from.

```
scripts/check-cluster-invariants.py
```

- `--hosts`, `--cluster-config`, repeatable `--vip-key`, `--lan-cidr-key`,
  `--allow-missing-cluster-config`, `--allow-missing-lan-cidr`,
  `--allow-missing-lan-addresses`, `--allow-missing-vip`,
  `--allow-missing-vmid`, `--allow-missing-ansible-host`.
- `--rules-dir` turns on the instance-parity arm, and takes an alert-rule file
  or a directory; repeat it for several. `--rules-glob` is the glob applied
  inside a directory (default `**/*.y*ml`). Every exact `instance="<ipv4>"` or
  `instance="<ipv4>:<port>"` literal must be some host's `ansible_host`, a
  cluster-config value, or a repeatable `--allow-instance` entry. `instance=~`
  is left alone: a regex alternation is deliberate, and the rules gates read it.
  An `--allow-instance` no rule pins any more is an operator error, so the
  allowlist keeps naming real exceptions, and a rules path matching no literal
  is one too.
- **Vendoring:** imports `inventory_tree.py` from its own directory, so the
  library gate and the consumer's own inventory gates cannot disagree about
  which hosts exist. Vendor the pair.
- With no `--vip-key`, the VIP arm checks both API-VIP spellings, both MetalLB
  VIPs, and every other `cluster_*_vip` key the config declares. An absent key
  is skipped.
- `--allow-missing-cluster-config` makes an absent cluster config a pass, and
  the success line then says the VIP and LAN arms were skipped. Without it, an
  absent config is an operator error, because two of the three arms cannot run.
- A config declaring no `--lan-cidr-key` is an operator error too: the LAN arm
  would examine nothing, and a misspelled key would leave it permanently green.
  `--allow-missing-lan-cidr` is the opt-out, and the success line then names the
  skipped arm.
- Every arm exits 2 rather than 0 when it examined nothing, so each has its own
  opt-out for a consumer whose inventory does not carry that field:
  `--allow-missing-vmid` (an inventory keeping `vmid` in `host_vars`),
  `--allow-missing-ansible-host` and `--allow-missing-lan-addresses` (an
  inventory addressing hosts by name), `--allow-missing-vip` and
  `--allow-missing-lan-cidr` (a config declaring neither), and
  `--allow-missing-cluster-config` (no config at all). The success line names
  each skipped arm, so an opt-out is visible rather than silent.
- Hosts are merged across groups, so a host in two groups is not a duplicate of
  itself; a host named rather than addressed is left to DNS.
- **Exit codes:** 0 clean, 1 on a collision, 2 on an operator error including an
  inventory with no host or a config declaring no VIP or no LAN CIDR.

### `check-role-inputs.py` (PyYAML, Jinja2)

Checks an Ansible inventory against the collection's role conventions: an opt-in
role (`<role>_enabled: false`) invoked unconditionally with the flag set
nowhere, a required input (`<var> | default('') | length > 0` asserted on
the role's default path) with no usable default and no assignment, and a
role-prefixed inventory variable no role or playbook reads. That third arm, the
unknown-inputs one, catches a typo or a variable the pinned collection renamed:
role variables are `| default()`-guarded, so such a variable fails inert rather
than loudly. It is static
— it reads `defaults/main.yml` and the roles' `assert` tasks rather than
replaying a play — so it under-reports rather than inventing work.

```
scripts/check-role-inputs.py \
    --roles-dir ~/.ansible/collections/ansible_collections/weisssrv/infra/roles \
    --inventory ansible/inventories/prod --playbooks ansible/playbooks
```

- `--roles-dir` accepts an installed collection path, so a consumer needs no
  library checkout. `--skip opt-ins` / `--skip required-inputs` /
  `--skip unknown-inputs` leaves one arm out; skipping all three is an operator
  error.
- `--allow-empty` turns an arm that found nothing to examine into a printed note
  instead of an operator error. A bootstrapping consumer that composes no opt-in
  role, or whose roles default every asserted input, uses it.
- `--allow-disabled ROLE=REASON` exempts one opt-in role from the first arm,
  repeatable and with the reason printed. It exists for a role like
  `vfio_passthrough`, whose disabled state is reconciled rather than skipped, so
  a consumer composing it unconditionally does not have to reach for
  `--skip opt-ins` and lose the arm for every other role.
- `--allow-unknown VAR=REASON` exempts one role-prefixed inventory variable that
  is set on purpose, repeatable and with the reason printed. A consumer mid-rename
  runs `--skip unknown-inputs` for one MR instead of allowlisting each variable.
- `group_vars` is read recursively, so the directory-per-group layout
  (`group_vars/<group>/<file>.yml`) and `.yaml` files are both seen. A file that
  does not parse is an operator error, not a silent omission.
- A default is judged by RENDERING it against the inventory: an input defaulting
  to an expression over a name the inventory does not set reads as supplied and
  evaluates to nothing.
- Ansible's `is match` and `is search` tests are modelled alongside `| bool`,
  so a `when:` or a default using either is judged rather than skipped.
- **An expression the evaluator cannot read is PRINTED, not dropped.** The
  required-inputs arm stays fail-open on a default or `when:` it cannot model (a
  lookup, a custom filter), but each one is listed as
  `UNMODELLED <role>: <var> default expression not modelled` and counted in the
  arm's summary line, so an input that left the required set is visible.
- **Exit codes:** 0 clean, 1 on a finding, 2 on an operator error including a
  scan that examined nothing.

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
  READMEs cross-link into `docs/`, so both halves of the link graph must be
  gated in one pass. Tracked-only is deliberate — untracked scratch Markdown is
  not ours to gate, and including it would make the check fail differently on
  every machine.
- **Fallback (not a git checkout):** everything under `docs/` plus the files
  named in `$CHECK_DOC_LINKS_EXTRA` (default `README.md CLAUDE.md`).
- **What is NOT checked:** URLs, `mailto:`/`tel:`, in-page anchors, and non-`.md`
  targets are all ignored, and the anchor part of a `file.md#section` link is not
  validated — only the file is.
- **Section citations:** a `docs/07-flux.md § Rotating a secret` or
  `(docs/07 § Rotating a secret)` pointer is resolved against that document's
  real headings, so a renamed section no longer leaves the pointer rotting. The
  document may be backticked or bare; a bare one must contain a `/`, so prose
  like "the role README § Metrics" names no path and is left alone. A numbered
  prefix (`docs/07`) resolves through the one matching file, and a `.jinja`
  source counts, nearest tree first, so a template repo does not resolve a
  generated repo's pointer against its own docs. The citation is cut at the
  punctuation that resumes the sentence, then compared word by word: either side
  may be a prefix of the other, since prose runs on and a line wrap cuts the
  name short. Set `$CHECK_DOC_LINKS_SECTIONS=0` to turn the arm off, for a
  template repo whose pointers describe the tree it generates.
- Stdlib-only and network-free, which is what lets every consumer vendor it.
- **Exit codes:** 0 all links and citations resolve, 1 on a broken link or a
  citation naming a missing heading, 2 on an operator error (the git enumeration
  failed, or no Markdown was found under the given roots).
- **Backticked repo paths** (`$CHECK_DOC_LINKS_PATHS=1`): a second pass resolves
  backticked repo-relative paths (`docs/01-overview.md`, `scripts/`) against the
  scanned root AND against the citing document's own directory, so a doc may
  cite a sibling tree (`references/x.md` from a skill README) and only a token
  that resolves under neither base is reported. A token qualifies only when its
  first path segment is a directory of one of those two bases, so absolute
  paths, `../` targets and prose are left alone; placeholders and quoted or
  bracketed tokens are skipped. Off by default
  because a doc may legitimately cite a path in another repo;
  `$CHECK_DOC_LINKS_IGNORE` holds space-separated fnmatch patterns for the
  exceptions that remain.
- **Consumer note:** `ci/lint/docs-link-check.yml` defaults `changes` to
  `**/*.md` plus the gate script, matching the checker's own tracked-Markdown
  scope. Pass your own `changes` only to narrow the trigger deliberately; a path
  missing from it means no run on that edit.

### `check-comment-length.py`

Fails a comment block longer than three content lines, so a comment stays a
description of the current state rather than a runbook. A block whose first line
opens `CRITICAL:` may run to eight, for the traps that cause an outage.

```
scripts/check-comment-length.py [PATH ...] [--config FILE]
    [--include GLOB] [--exclude GLOB] [--max N] [--critical-max N]
    [--scan-fenced-code] [--strict-jinja]
```

- **Scan scope:** the paths given, else the config's `paths`, else the working
  directory. Blank lines and the comment markers themselves do not count, and a
  suffix listed in neither the line-marker nor the block-delimiter map is
  skipped, so a new language opts in deliberately. `.j2` registers `#` and `//`
  as line markers plus `{# #}` blocks, because a template renders into whatever
  language its consumer needs. `Dockerfile.<stage>` resolves to the plain
  Dockerfile markers, and a copier `.jinja` source resolves to the file it
  renders into, so `main.tf.jinja` is scanned as Terraform.
- **Where a block ends:** a blank line. A bare `#` line keeps the same block
  going, so two paragraphs separated only by a `#` are counted as one block.
  Split them with an empty line to have them counted separately.
- A `#!` line is the shebang, not a comment, while only blank lines or jinja
  tag lines precede it, so a `*.sh.jinja` that opens with a `{% if %}` seam
  keeps its shebang exempt.
- A Dockerfile's leading `# syntax=` and `# escape=` lines are parser
  directives, not prose: they neither count toward the limit nor join the
  header block below them. Only the preamble is read that way, so the same
  spelling further down the file is an ordinary comment.
- **`--scan-fenced-code`** (config `scan_fenced_code`) applies the limit inside
  Markdown fenced code blocks too, so a wiring snippet people copy into a real
  file is held to it. The fence's language tag resolves to a suffix through the
  fence-language table, and a language the table does not list is skipped.
- **A `.py.jinja` source** has its jinja tags neutralized before the parse that
  reads docstrings, so a copier template is held to the same limit as the file
  it renders into. A `{% raw %}` body is kept verbatim, because jinja reads no
  tags inside it. One that still does not parse is a single WARNING and no
  change to the exit code; **`--strict-jinja`** makes it an operator error.
- **Config keys:** `paths`, `include`, `exclude`, `max_lines`,
  `critical_max_lines`, `scan_fenced_code`. Template:
  [`examples/comment-length.example.yml`](../examples/comment-length.example.yml).
  Flags win over the config; `.git`, `.terraform`, `.venv`, `.ansible`,
  `.ansible-home`, `__pycache__`, `node_modules`, the
  Python tool caches (`.pytest_cache`, `.ruff_cache`, `.mypy_cache`),
  `*.egg-info`, `.tmp` and `.worktrees` are always excluded, so scanning `.`
  does not fail on a tree the repo did not author.
- Stdlib-only, so every consumer can vendor it.
- Consumers include it as `ci/lint/comment-length.yml`. Adopt it after a sweep:
  a repo with a backlog of long comments reds every pipeline from the first run.
- **Exit codes:** 0 clean, 1 on an over-long block, 2 on an operator error (a
  missing path or config, a `max_lines` below 1 or above `critical_max_lines`,
  a file that does not decode, a `.py` that does not parse, or a run that found
  no scannable file). An unparsable `.jinja` source is a warning unless
  `--strict-jinja` is given.

### `check-role-defaults-documented.py` (PyYAML)

Fails a role whose `defaults/main.yml` declares a variable its README never
names. A variable with a default and no documentation is a knob nobody knows
exists, and the role README's variables table is the collection's API surface.

```
scripts/check-role-defaults-documented.py [--roles-dir DIR] [--table-only]
```

- `--roles-dir` defaults to `ansible_collections/weisssrv/infra/roles`, so a
  consumer points it at its own roles directory or an installed collection.
- `--table-only` requires a variables-table row rather than a mention anywhere
  in the prose, for a consumer holding its role READMEs to the one skeleton.
- The match is a whole-word one: a variable named anywhere in the README passes,
  but a longer name that merely contains it (`foo_bar_baz` for `foo_bar`) does
  not, which is what a half-finished rename leaves behind. A role with defaults
  and no README is an operator error, not a finding.
- **Exit codes:** 0 clean, 1 on an undocumented variable, 2 on an operator error
  (a missing roles directory, unparseable defaults, or a scan that examined no
  role).

### `check-role-readme-literals.py`

Fails a role README that documents one site's wiring instead of an illustrative
value. The collection serves every consumer, so a `192.168.` address or a site
domain in a role README is a value the next operator copies by mistake.

```
scripts/check-role-readme-literals.py [--roles-dir DIR] [--site-domain NAME]
    [--no-site-domains] [--site-literal REGEX] [--no-site-addresses]
```

- Scope is `<roles-dir>/*/README.md` only. `MIGRATING.md` legitimately quotes a
  consumer's old values as migration examples, so it is never scanned.
- `192.168.0.0/16` addresses and `pve-<word>-nn` hostnames always fail. Product
  spellings such as `pve-firewall` are not hostnames and pass.
- A consumer passes its own domains with `--site-domain` (repeatable), or
  `--no-site-domains` when it has none. With neither, the run is an operator
  error, because the domain arm would check nothing.
- `--site-literal REGEX` (repeatable) adds a consumer's own naming scheme
  without a code change. This repo's own CI passes its LAN range that way:
  `--site-literal '(?<![\w.])10\.0\.(10|20)\.\d{1,3}(?!\.?\d)'`.
- A run with neither `--site-literal` nor `--no-site-addresses` is an operator
  error, because the address arm would only cover `192.168.0.0/16`. Pass
  `--no-site-addresses` when that range IS the site's.
- **Exit codes:** 0 clean, 1 on a literal, 2 on an operator error (a missing
  roles directory, one holding no role README, no domain flag, or no site-range
  flag).

### `check-taskfile.sh`

Asserts every `scripts/<name>.{sh,py}` a Taskfile references exists on disk,
plus each `dotenv:` target, each task a `cmds:`/`deps:` entry names, and that
every fragment on disk is reached by the root `includes:`. go-task compiles
command templates lazily and never stats a referenced file, so a renamed script
is invisible to `task --list` — and a missing dotenv file makes go-task fail
hard at load time, taking every task with it.

```
scripts/check-taskfile.sh [Taskfile.yml ...]  # default: <repo-root>/Taskfile.yml
```

- **`includes:` are followed.** Both the `name: path.yml` shorthand and the
  `name: {taskfile: path.yml}` map form, resolved relative to the including
  file, with a visited set for cycles and a depth cap
  (`CHECK_TASKFILE_MAX_DEPTH`, default 10). Only a column-0 `includes:` block
  counts. This covers a consumer's own vendored fragments, which carry their own
  `scripts/` references.
- **A missing include target is a failure**, not a skip: go-task fails hard at
  load time on one, taking every task with it.
- **Env:** `CHECK_TASKFILE_DOTENV` — space-separated dotenv targets to require
  when the Taskfile references them. Default `scripts/hosts.env`; set it to the
  consumer's own generated env file(s), or to an empty string for a Taskfile
  with none.
- The dotenv match is on the bare path anywhere in the file, not just a same-line
  `dotenv:`, so the YAML multi-line list form is caught too.
- **Task references are resolved across the whole tree**, from both `cmds:`
  (`- task: NAME`) and `deps:` (a bare string, a `- task: NAME` mapping, or the
  inline `deps: [a, b]` list). A bare name qualifies against the including
  namespace and a leading-colon name against the root, the way go-task resolves
  them, so a dangling entry fails here rather than at the moment an operator
  runs the task. A reference holding `{{` is templated and skipped.
  `CHECK_TASKFILE_REFS=0` turns the arm off.
- **A fragment no `includes:` entry reaches is a failure.** Such a file is
  wholly inert: `task --list` omits every task in it and no other gate fires.
  `CHECK_TASKFILE_FRAGMENT_DIR` names the directory scanned (default
  `taskfiles`); set it to an empty string for a repo that keeps none.
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
  pin and fails. The entry is located from the parsed node tree, so a `version:`
  under a different collection is never matched, and it is matched on the
  **repository NAME** of its `name:` or `source:` — the last path segment,
  lower-cased and `.git`-stripped — never as a substring of the URL. That keeps
  an instance-local mirror of the same repository on another host gated while a
  FORK, or a mirror whose URL merely contains the project path
  (`…/mirrors/eric/weisssrv-lib-fork.git`), is a different repository and does
  not count as installing the library. A requirements.yml that declares a git collection
  matching nothing fails with `no git collection matching <project>` rather than
  passing silently; one with no git collection at all stays a no-op.
- **`--fix`** rewrites the literals to the single source — both the `include:`
  refs and the collection `version:` — and reports the two counts together
  (`rewrote N pin(s) in <ci-file> + requirements.yml`). A bump is one edit plus
  one command. The rewrite is textual, so comments and formatting survive,
  but the lines it touches come from the PARSED tree — the `ref` key of each
  direct mapping under `include:`, exactly the nodes `check()` reads. Every
  indentation heuristic tried here leaked (`inputs:` may carry its own `project`
  and `ref`), so the two halves agree by construction rather than by scanning.
- **`--fix` refuses rather than half-repairs.** Four checks, any of which
  aborts before a byte is written:
  - the source value is validated first, so a branch ref is reported instead of
    propagated to every include;
  - the output is re-parsed and every pin must have landed as the exact string
    intended;
  - targets are bounded to the `include:` block's own span, so an aliased entry
    cannot redirect the rewrite at an anchor elsewhere in the file;
  - an **alias** inside `include:` — or an anchor DEFINED there, which something
    outside can reference — refuses the whole block, because composing resolves
    aliases away and the node tree cannot show either.

  Where it cannot repair — a missing `ref:`, a flow-style entry, a pin outside
  `include:`, an aliased block — it says so and leaves the file untouched
  instead of returning a clean 0. An alias elsewhere in the file does not
  disable it.
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

### `check-live-cpu-limits.py` (PyYAML via gate_common)

Reads the LIVE cluster, not the rendered manifests: a `kubectl get ... -o json`
payload on stdin. Fails any container running with a `limits.cpu`, and warns
when a pod's live memory limit differs from its workload template.

```
kubectl get pods,deployments,statefulsets,daemonsets,verticalpodautoscalers \
  -A -o json | scripts/check-live-cpu-limits.py
```

- **Why live:** a limit can arrive from a chart default, a stale server-side
  apply owner or a mutating VPA, none of which a static gate over the manifests
  can see. CFS throttling hurts tail latency, and on a VPA-managed workload the
  limit shrinks with every request revision.
- **Pods alone are enough** for the CPU-limit arm; pass the workload templates
  and the VPAs too and the memory-drift arm runs as well. Without them the
  success line says the drift arm was skipped.
- A mutating VPA applies at pod ADMISSION, so pods older than the commit keep
  the pair they were admitted with. Containers whose limits an active VPA owns
  are skipped, and the skip count is printed so a shrinking findings list cannot
  be mistaken for a fixed cluster.
- `LIVE_CPU_LIMIT_ALLOWLIST` in the script is empty by design, keyed
  `namespace/pod-name-prefix/container`. An entry is a claim that CFS throttling
  is WANTED there, and needs a reason beside it.
- **Exit codes:** 0 clean (memory drift is a warning only, so it does not own
  the exit code), 1 on a live CPU limit, 2 on an operator error. The empty-input
  arm comes from `gate_common.load_live_items`: a valid but EMPTY item list is
  exit 2, because a wrong context or selector returns one and the run would
  otherwise read as a clean cluster.
- **Vendoring:** imports `gate_common.py` from its own directory. Vendor the
  pair, or it exits 2 on import.

### `check-molecule-image-pin.py` (PyYAML)

Asserts every hand-written `<project>/molecule-{test,ci}:<tag>` literal in the
molecule scenarios and `ansible/TESTING.md` equals the same single source
`check-lib-pins.py` reads (`variables.WEISSSRV_LIB_REF`). `--fix` rewrites them.

CI exports `MOLECULE_TEST_IMAGE` and overrides the literal, so only a LOCAL
integration run reads it: a stale tag runs the local suite against an old image
while the pipeline stays green. That is the failure this catches.

- **Forge: gitlab-only**, for the same reason as `check-lib-pins.py`: the value
  comes from `variables.WEISSSRV_LIB_REF` in `.gitlab-ci.yml`.
- **Flags:** `--ci-file PATH` (default `.gitlab-ci.yml`; its directory is the
  tree that is scanned and rewritten), `--project` (default
  `eric/weisssrv-lib`), `--ref-var` (default `WEISSSRV_LIB_REF`), `--source
  GLOB` (repeatable; default the molecule scenarios and `ansible/TESTING.md`),
  `--fix`.
- The match is anchored on the image PATH, so an unrelated `:v1.2.3` elsewhere
  in the same file is never rewritten, and on a `v`-prefixed tag, so a `:local`
  sentinel is not read as a stale pin.
- **Exit codes:** 0 consistent, 1 on drift, **2** for an operator error — an
  unreadable or malformed CI file, a `ref-var` that is unset or is not a release
  tag, or **no literal found at all**, which means the fallback moved or
  `--project`/`--source` is wrong rather than that everything is fine.
- **Consumers vendor it** and run it beside `check-lib-pins.py` in `task lint`,
  with the same `changes:` rules.

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
    [--lib-path DIR] [--ref GIT_REF] [--require-ref] [--list]
    [--scan CONSUMER_DIR=LIB_PREFIX]
```

- **Two relationships.** `vendored` is byte-identical — drift in either
  direction, a missing local copy, and a file the library dropped all fail.
  `forked` is deliberate divergence: the entry must still DIFFER (a converged
  fork belongs under `vendored`) and, when it records `reconciled_sha256`, the
  LIBRARY side must not have moved since the fork was last reconciled. That last
  arm is what a documentation-only fork list cannot catch.
- **A prose-only fork fails too.** A fork whose only divergence is comment and
  blank lines fails the "must still DIFFER" arm, so a `reason:` that no longer
  describes anything real is caught: move it to `vendored:` and re-vendor, or
  declare the prose difference
  with `comment_only: true` (a header a consumer deliberately rewrites, as the
  Python toolchain pins do). `comment_only: true` on a fork that does diverge
  in code fails the other way, so the declaration stays honest. Comparison is
  by whole-line comment markers per suffix; a suffix the gate has no markers
  for is never reported this way, and a first-line `#!` counts as code.
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
- **`--scan CONSUMER_DIR=LIB_PREFIX`** (repeatable) reports a file under that
  consumer tree whose offered library twin at `LIB_PREFIX/<relpath>` no manifest
  entry registers, so an unregistered copy is caught rather than drifting
  unguarded. A `--scan` naming a directory that does not exist is an operator
  error, never a silent skip.
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
- **`--require-ref`** exits 2 unless `--ref` was given and resolves. Without a
  resolving ref the compare target is the library working tree, so a clean run
  proves nothing about the pinned release; the summary line says
  `REF UNVERIFIED` rather than `OK at <ref>` in that case. The permissive
  default stays, for the window before the tag is cut; a consumer job that
  demands a verified comparison passes the flag. A dirty working tree does not
  affect a verified run, because every library byte then comes from the ref.
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
- **Companion:** imports `ci_yaml.py` from its own directory. Vendor both;
  without it the gate exits 2 naming the missing file.
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
- **A `**` wildcard confers no coverage credit** for playbooks or inventory
  paths — only a verbatim `<dir>/<path>.yml` entry in a deploy job's `changes:`
  list does. A single `ansible/playbooks/**` would otherwise mask a missing
  trigger for every newly added playbook. Roles are the exception: any
  `<roles_dir>/<name>` prefix counts, because a role is a directory.
- **Example:** [`deploy-coverage.example.conf`](../examples/deploy-coverage.example.conf).

### `check-deploy-preflight.py` (PyYAML)

Proves every deploy job's `ansible-playbook` call would do work: parses the argv
of each invocation in a job extending a deploy base and runs `--list-tasks` per
`--tags` selection, so a tag that selects no task fails instead of reporting a
silent no-op. A job written in a shape the parser cannot read fails rather than
shrinking the check.

```
scripts/check-deploy-preflight.py --require-tag-selections
```

- **Forge: gitlab-only** — it reads deploy jobs out of a GitLab CI file through
  `scripts/ci_yaml.py`, so a `!reference`d script block is expanded.
- `--ci-file`, `--ansible-dir`, repeatable `--extends` (default `.deploy-base`
  `.maintenance-base`), `--require-tag-selections` (off by default; the
  `0 playbooks` guard is unconditional).
- Needs `ansible-playbook` on PATH and the collections the playbooks reference.
- The job's own `--limit` and `--skip-tags` are passed on to every probe, so a
  tag whose every task the job skips reads as the no-op it is rather than
  scoring as selected.
- **Exit codes:** 0 clean, 1 on a finding, 2 on an operator error.

### `check-version-checksums.py` (PyYAML)

Verifies every checksum pin the version registry declares. An entry carrying
`checksum_var` + `checksum_url` couples a hash in the vars file to a version in
the same file; the gate renders the URL at the pinned version, downloads it and
compares the sha256. Accepts `sha256:<hex>` or bare hex. Declaring one half of
the pair, or pairing it with a `version_file` pin, is an operator error.

Needs network egress, so wire it into a CI lint job rather than an offline gate.
`checksum_url` must be `https://`; a plaintext fetch is refused. The registry
path is resolved the way `check-versions.py` resolves it, so `--config` and
`$CHECK_VERSIONS_CONFIG` both work, and the two scripts are vendored as a pair.

```
check-version-checksums.py [--config scripts/version-registry.py]
    [--repo-root .] [--vars-file <path>] [--allow-empty]
```

- `--allow-empty` makes a registry with no checksum pin a pass. Without it, a
  registry that declares none is an operator error, because the gate then
  verified nothing.
- **Exit codes:** 0 clean, 1 on a stale pin, 2 on an operator error.

### `ci-fetch-tools.py`

Installs pinned CI tool binaries into a workspace bin, with no root and no
package manager, for the jobs that run as an unprivileged user on an image
where `apt` is unavailable. Each tool carries a version, a linux-amd64 asset URL
and the sha256 of that exact asset. The download is verified before anything is
extracted, only the one named member comes out of an archive, and the binary is
renamed into place once it is complete. Stdlib only; needs network egress.

```
ci-fetch-tools.py [--dir DIR] [--list] [--force] TOOL...
```

Tools: `amtool`, `jq`, `kubeconform`, `kustomize`, `promtool`, `shellcheck`,
`terraform`. Each prints one line on success, `jq 1.8.2 -> /path/.bin/jq`.

- **`DIR` defaults to `$CI_PROJECT_DIR/.bin`**, else `./.bin`, and is created if
  missing. **The caller adds `DIR` to `PATH`**: the script installs binaries and
  deliberately changes nothing about the environment of the job that ran it.
- **Each install stamps `DIR/<name>.version`** with the effective version and
  sha256, and a tool counts as present only when that stamp matches the resolved
  pin — so a skip prints `jq 1.8.2: present`, and a cached, pre-seeded,
  truncated or differently-versioned binary is re-fetched instead of trusted.
  That is what makes a `cache:` on `DIR` safe: a version bump still takes
  effect. The stamp lands after the binary, so an interrupted install reads as
  absent. `--force` re-installs regardless, and a job can call the script
  repeatedly.
- **The pinned versions are this script's own**, not a consumer's, and they are
  not site data — the same pins serve every consumer. A consumer re-pins one
  tool with `TOOL_<NAME>_VERSION` and `TOOL_<NAME>_SHA256` (the name
  upper-cased, so `TOOL_JQ_VERSION` and `TOOL_JQ_SHA256`) rather than forking
  the file.
- **An overridden version needs its own checksum.** Setting the version alone
  is an operator error, because the table's sha256 belongs to the version the
  table names and would reject the asset that arrives.
- `--list` prints the table — name, version, sha256 prefix — with any env
  override already applied, and exits 0. It takes no tool argument.
- **Exit codes:** 0 on success, 2 on an unknown tool name (the message lists the
  known ones), a download failure naming the URL, a sha256 mismatch, an archive
  whose named member is missing or escapes the archive, or a half-made
  override. A mismatch leaves no file in `DIR`.

### `check-molecule-matrix-coverage.sh` (PyYAML)

Fails when a molecule scenario dir or an integration-test dir exists with no
matching `parallel:matrix` entry, when a matrix entry names a scenario or test
dir that does not exist on disk (the job would fail at runtime), when a role has
no runnable scenario at all, or when the matrix exceeds `MAX_MATRIX_ENTRIES`
(default 45 — an aggregate job that `needs:` every entry hits GitLab's hard
50-needs-per-job limit).

- **Both directions.** Disk-without-matrix is a silently untested scenario;
  matrix-without-disk is a job that fails at runtime or, worse, runs nothing.
  The gate reports each separately and names the path it expected.
- **Env:** `CI_FILE`, `ROLES_DIR`, `INTEGRATION_DIR`, `MOLECULE_JOB`,
  `INTEGRATION_JOB`, `UNTESTED_ROLES`, `MAX_MATRIX_ENTRIES`.
- **`ROLES_DIR=""`** (explicitly empty, not unset) declares that the consumer
  has no in-repo roles because they ship from the collection. The molecule half
  is then disabled — no on-disk scenario scan, no untested-roles check, no
  matrix comparison, no `MAX_MATRIX_ENTRIES` cap — a one-line note says so, and
  the integration-test half still runs. A NON-EMPTY `ROLES_DIR` that does not
  resolve is a typo and still exits 2.
- Parses the CI file with `scripts/ci_yaml.py`'s `CILoader`, so GitLab's
  `!reference` tags survive the parse and the global `SafeLoader` is never
  mutated. A CI file that is not a YAML mapping exits 2, not 1.
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
  collection-wide change would select nothing and report green. `galaxy.yml` is
  the one exception: a diff touching nothing but its `version:` key is dropped
  from the changed set with a note, because every release MR bumps it and the
  version lineage bears on no scenario. That needs a diff base — with
  `--changed-files-from` or piped paths there is nothing to diff and `galaxy.yml`
  stays a full trigger. `CI_FILE` and
  `MOLECULE_JOBS_INCLUDE` are triggers too. A repo with no integration suite just
  omits the `integration-tests` job from `CI_FILE`; a job that IS present with a
  broken matrix still fails loudly.
- **The rest of the default trigger set is the conventional layout, not a
  derivation**: `scripts/molecule-retry.sh`, `scripts/generate-molecule-pipeline.py`,
  `ansible/molecule/`, `ansible/playbooks/maintenance/`, `docker/molecule-test/`
  and `docker/molecule-ci/`. A repo that keeps its image contexts or helpers
  elsewhere would otherwise carry dead triggers *and* a full-matrix fan-out on
  any `docker/` change, so `MOLECULE_GLOBAL_TRIGGERS_MODE=replace` drops that set
  and takes `$MOLECULE_GLOBAL_TRIGGERS` as the whole of it. The derived and
  CI-file entries above are not replaceable. An unknown mode value fails the job
  rather than being ignored.
- **Library-internal, not on the offer list.** A consumer reaches this generator
  through `ci/validate/`'s image rather than a vendored copy, so
  `scripts/vendorable-paths.yml` does not offer it and no manifest names it.
- **`$MOLECULE_GLOBAL_TRIGGERS` must be paired with the plan job's `changes:`.**
  A path listed here forces a full matrix *once the plan job runs* — but if that
  path is not also in the `changes:` list that creates the plan job, an MR
  touching only that path creates no plan job at all and the full matrix never
  happens. The two lists are one setting expressed in two places; change them
  together.
- **How the changed-file list is obtained**, first match wins:
  `--changed-files-from FILE` (`-` = stdin); then a base SHA (positional or
  `--diff-base`), run as `git diff --name-only <base>...HEAD` — three-dot,
  matching `CI_MERGE_REQUEST_DIFF_BASE_SHA` semantics; then stdin when it is not
  a TTY.
- **Single source of truth.** The scenario universe is the two `parallel:matrix`
  blocks in the CI file, read-only — the same matrix
  `check-molecule-matrix-coverage.sh` enforces, never re-hardcoded.
- **Fail loud, never under-select.** A changed path under `<roles-dir>/<name>`
  where `<name>` is not in the matrix raises; so does an unparseable or empty
  molecule matrix, an `integration-tests` job whose matrix is empty or malformed
  (only its complete ABSENCE reads as "no integration suite"), a selected role
  with no matrix scenarios, and a YAML file that is present but unreadable. When
  in doubt it selects MORE: any global-trigger path selects everything, and the
  direct role→scenario mapping is a floor.
- **Inventory paths** are handled by a derived inventory-consumer map (scenarios
  are scanned for inventory-file references, resolved relative to the
  referencing file), so a `group_vars` file selects only the scenarios that load
  it.
- Wired by `ci/internal/molecule-matrix.gitlab-ci.yml`.

### `maintenance-run-with-verify.sh`

Runs one maintenance command, then runs the consumer's verify script whatever
the command's outcome, and exits with the command's rc if it failed, else the
verify's. A maintenance job that short-circuits on failure leaves the cluster
unexamined at exactly the moment the state matters.

```
bash scripts/maintenance-run-with-verify.sh ansible-playbook -i inventories/prod upgrade.yml
```

- Takes one program with its arguments, not a pipeline: wrap one as
  `bash -c 'set -o pipefail; a | b'`.
- **Env:** `VERIFY_SCRIPT` retargets the verify script (repo-relative or
  absolute); the default is the consumer-owned
  `scripts/post-maintenance-verify.sh`. `CI_PROJECT_DIR` sets the repo root,
  else it is derived from the wrapper's own location.
- The verify is invoked as `bash "$VERIFY"`, so readability is enough and a
  checkout that dropped the `+x` bit still works. An unreadable one exits 64
  rather than reporting a run nothing verified.
- The verify script itself stays consumer-owned: what "healthy" means is
  per-repo.
- **Exit codes:** the command's rc when it failed, else the verify's, and 64 on
  a usage error (no command, or no readable verify).

### `molecule-retry.sh`

Runs `molecule test` with an in-job destroy + jittered retry (concurrent
systemd-container starts race cgroup setup and die at prepare). Env: `MOL_MAX`
(4), `MOL_BASE` (args before the subcommand), `MOL_SCEN` (args after it),
`JUNIT_OUTPUT_DIR`, `MOLECULE_RETRY_DOTENV`.

- **Only the deciding attempt is reported.** A failed attempt's
  `$JUNIT_OUTPUT_DIR/*.xml` are MOVED to
  `$JUNIT_OUTPUT_DIR/failed-attempt-<n>/`, not deleted: the job's
  `reports: junit: junit/*.xml` glob does not reach a subdirectory, so the test
  report still reflects the attempt that decided job status while the failed
  attempts stay downloadable when the job also publishes `artifacts: paths:`
  for them.
- **`MOLECULE_RETRY_DOTENV`**, when set, is written as
  `MOLECULE_RETRY_ATTEMPTS=<n>` on both the success and the exhausted path, so a
  job can expose the retry count with `reports: dotenv:`. Unset means no report.
- **The retry is stage-scoped.** Only the setup stages retry — dependency,
  cleanup, destroy, syntax, create, prepare. Once molecule prints a stage banner
  at or after `converge`, the wrapper exits with molecule's return code
  immediately, so a flaky assertion cannot be re-rolled into a green job. The
  attempt count is printed on every exit path.

### `sanitize-junit-expected-failures.py`

Downgrades junit `<testcase>` failures whose name matches a substring declared in
the scenario's `expected-junit-failures.txt` (negative-path tests that a
block/rescue handles). Undeclared failures stay red. A missing declaration file
is a no-op.

```
sanitize-junit-expected-failures.py --junit-dir junit --expectations <file> [--strict]
```

- **`--strict` fails (exit 1) when a declared expectation matched no testcase.**
  A renamed or deleted guard task otherwise leaves its declaration behind and
  the negative path silently stops being exercised. Without `--strict` an
  unobserved declaration is only a warning, so a caller that passes
  `--expectations` unconditionally is unaffected.
  `tests/test_expected_junit_failure_declarations.py` is the static half of the
  same guard: it holds every declared line to a matching task name in the role
  or the declaring scenario, on every run rather than only in a selected
  scenario.
- **A line may end in ` ::<n>` to declare how many testcases it matches**; a
  plain line declares one. `<n>` must be a positive integer, or the suffix is
  read as part of the pattern and nothing matches it.
- **Under `--strict` the count is exact, in both directions.** Matching more
  than declared means the pattern names more than the one guard, or the guard
  really fails that often and the count is stale: narrow the pattern or raise
  the count. Matching fewer means a negative case stopped firing, or the count
  was always wider than the guard can produce: lower it, or restore the case.
  The error names the observed and declared numbers either way.
- **Without `--strict` the count is only a cap, and a mismatch is a warning.**
  So declare the number the run records, never a margin: a count wider than the
  guard can produce downgrades a real extra failure of that same guard, and
  outside strict mode nothing fails to say so.
- **Count what the run records, not what the scenario reads like.** The junit
  callback writes one testcase per task per host, so a guard that fires on two
  platforms counts twice; a guard driven from several negative cases in one
  playbook counts once per case; and a scenario whose `test_sequence` includes
  `idempotence` replays converge, so a converge-driven guard counts twice unless
  its task or an enclosing block carries the `molecule-idempotence-notest` tag.
- **A case driven under `ignore_errors: true` is recorded as passed**, so it is
  never observed and must not be declared. Drive a negative case with
  `block`/`rescue` instead.

---

## Object storage

### `b2-bucket-drift.py`

Codified settings for a Backblaze B2 bucket (type, SSE, lifecycle rules,
retention) with a drift check and a supervised `--apply` (interactive
confirmation required; a bad lifecycle rule can expire the only offsite copy).

- **Config:** `--config` (default `b2-bucket.json`): `account_id`, `bucket_id`,
  `bucket_name`, `desired`.
- **Env:** `B2_APPLICATION_KEY_ID`, `B2_APPLICATION_KEY`.
- **Why no Terraform provider.** The Backblaze provider's READ path returned
  empty attributes against B2's current API (writes applied; every refresh and
  data source nulled `bucket_type`, SSE and lifecycle), so a plan reported a
  permanent phantom "1 to change". The raw API reads and writes the same
  settings correctly. Re-check this before assuming it still holds.
- **Optimistic concurrency.** `--apply` sends the `revision` read from
  `b2_list_buckets` as `ifRevisionIs`, so a change made in the B2 console
  between the read and the write loses the write rather than being clobbered. B2
  answers a stale revision with HTTP 409; the script prints "the bucket changed
  since it was read; re-run the drift check" and exits 2.
- **Example:** [`b2-bucket.example.json`](../examples/b2-bucket.example.json).

### `unifi-settings-drift.py`

Fails when a UniFi settings section the console owns drifts from its codified
expectation. Terraform sets such a section once and then ignores it, so the only
record of the intent is this config and nothing else notices a console edit.

```
scripts/unifi-settings-drift.py [--config FILE]
```

- **Config** (default `unifi-settings.json` beside the script): `site`,
  `desired` (a non-empty object of the keys to pin), and `section` (one path
  segment, default `ips`). Only the declared keys are compared, so the volatile
  ones the controller maintains are left unread.
- **Env:** `UNIFI_API_URL`, `UNIFI_API_KEY`. `UNIFI_ALLOW_INSECURE=1` turns TLS
  verification off and says so on stderr.
- **Only the section-addressed `/get/setting/<section>` is read.** The
  unsectioned `/rest/setting` returns the device SSH password, its hash and the
  site API token in cleartext, so the response body is never printed and every
  reported value is truncated.
- A list is compared order-insensitively, since the controller reorders one
  freely.
- **Exit codes:** 0 clean, 1 on drift naming each key with the live value first,
  2 on an operator error (bad or missing config, missing credentials, a failed
  read reported by exception type rather than by body).
- **Example:** [`unifi-settings.example.json`](../examples/unifi-settings.example.json).

---

## Shell helpers

| Script | Contract |
|---|---|
| `shell-lib.sh` | function-only (safe to source under `set -e`): `timeout_cmd <secs> <cmd…>`, `ssh_probe <target> <cmd>`. With neither `timeout` nor `gtimeout` on `PATH` it warns **once per shell** on stderr that probes will run unbounded, rather than silently dropping the bound — anything parsing stderr from a sourcing script (the two finders below here; consumers source it from their own scripts too) sees that line. `kubectl_read <args…>` reads one object as JSON and separates the three outcomes a swallowed read cannot: 0 with the JSON on stdout, 3 on NotFound, 1 with kubectl's stderr on an unreachable cluster, a denied read or an API timeout. `captured_match <value> <pattern>` tests a value already in hand: `cmd | grep -q` under `pipefail` exits on the first match, SIGPIPEs the producer and returns its status, so the pipeline turns a match into a failure. Guest-smoke and probe scripts use the helper rather than the pipe. `url_contains <url> <bre>` fetches then tests a BRE, `ssh_contains <target> <cmd> <bre>` does the same over SSH; `SHELL_LIB_CURL_MAX_TIME` bounds the fetch (default 10s) |
| `smoke-lib.sh` | the probe half of a per-guest smoke script, which stays consumer-owned because the endpoint list is site data. `http_status <url>` prints a HEAD request's status or `000` when nothing answered, and **always returns 0** so an errexit caller reaches the classifier. `check_http_ok` takes 2xx/3xx, `check_registry_ok` also takes 401 (a container registry answering without credentials is up), `check_http_below_500` takes anything that answered below 500. `check_tcp_port <host> <port>` is a bounded `nc` probe. `smoke_url_contains` / `smoke_ssh_contains` capture then test a **literal** needle, unlike `url_contains` / `ssh_contains` above, which take a BRE; `smoke_ssh_matches` takes an ERE for an anchored needle. `smoke_check <label> <cmd…>` and `smoke_optional <label> <reason> <cmd…>` keep the `SMOKE_PASS` / `SMOKE_FAIL` ledger, and `smoke_summary` prints it and is the script's exit status. `SMOKE_LIB_CURL_MAX_TIME` bounds every fetch |
| `ci-run-check.sh` | function-only driver a consolidated gate job sources: `run_check <name> <cmd…>` runs one check in a `set -eo pipefail` subshell with errexit toggled off around it, so a failing check records itself in `overall`/`failed` and the next check still runs; `run_check_summary [label]` names every failure and returns the job's status. The job sets `set -euo pipefail` itself |
| `find-reachable-host.sh` | prints the first reachable SSH target from its args, exit 1 if none |
| `find-pve-host-for-vm.sh` | prints which Proxmox host runs a VMID (ha-manager → `pvesh /cluster/resources` → per-host `qm status`) |
| `resolve-tool.sh` | prints how to invoke a Python dev tool (`PATH` → `python3 -m <module>` → validated pyenv glob) |
| `cluster-config-value.sh` | prints one or more `data:` values from the cluster-config ConfigMap, space-separated; fails on an absent key rather than printing nothing. `$CLUSTER_CONFIG` overrides the path |
| `collect-state-lib.sh` | function-only helpers a consumer's `collect-state.sh` sources: `REDACT_PATTERNS` + `redact_file`, the `cs_capped` / `cs_emit` section emitters, `warning_events_filter`, `coerce_int`, and the `classify_regular` / `regular_failing_predicates` / `classify_json` verdicts. Detail below |
| `inventory_tree.py` | importable Ansible-inventory resolver (PyYAML): `group_index(inventory)` indexes every group that carries content, merged across occurrences; `resolve_hosts(name, index)` expands a group in inventory order with children depth first, deduped, and terminates on a cycle (`strict=True` raises `InventoryCycle`); `declared_groups` also counts a null-bodied placeholder; `host_vars` merges a host's vars across every group listing it; `all_hosts`, `hosts_by_address` and `addresses_by_host` read off that. `generate-hosts-env.py` imports it from its own directory: vendor the pair |
| `ci_playbook_invocations.py` | importable argv walk over a job script's `ansible-playbook` calls: `parse_invocations(text)` returns one dict per call (`inventory`, `playbook`, `limit`, `tags`, `skip_tags`, `argv`). Every call is returned, so two chained on one script line are two invocations, and `--skip-tags` is kept out of `tags` so a gate never reads a skip as a selection. Stdlib only. `check-deploy-preflight.py` imports it from its own directory: vendor the pair |
| `ci_yaml.py` | importable loader for a `.gitlab-ci.yml` that uses GitLab's `!` tags: `CILoader` preserves a tagged node's structure and turns `!reference` into a resolvable `Reference`, `NullTagCILoader` collapses every tagged node to None; plus `load_ci`, `parse_ci`, `jobs`, `script_lines`. Both subclass SafeLoader and neither registers a constructor globally. **Forge: gitlab-only** |

### `collect-state-lib.sh`

The pure-logic half of a cluster-state collector. The collector itself stays
consumer-owned, because its section list is site data; what is shared is the
redaction guard, the output emitters and the verdict ladder.

- **Redaction:** `REDACT_PATTERNS` is a sed-program array and `redact_file
  <in> <out>` applies it, then collapses whole PEM private-key blocks in a
  second awk pass. POSIX character classes throughout, so it behaves the same
  on BSD and GNU sed. A consumer adds its own patterns by appending to the
  array after sourcing.
- **Emitters:** `cs_capped <cap> <fallback>` prints at most `cap` stdin lines
  (`0` uncapped), the fallback when stdin was empty, and an explicit truncation
  marker when the cap clipped; it consumes the whole stream so the producer
  never takes SIGPIPE. `cs_emit <fallback>` is the uncapped form. They replace
  the `producer | head -N || echo MSG` idiom, which can never print `MSG`
  because the pipeline exits with `head`'s status.
- **`coerce_int <value> <fallback>`** is the probe contract: a probe that could
  not answer returns a non-numeric value, and this coerces it to the caller's
  fallback — `1` for a verdict input, so an unaskable query degrades instead of
  reading as a clean zero, and `null` for JSON output, so a consumer can tell
  "none" from "could not ask". **A probe must never return 0 for "unknown".**
- **`warning_events_filter <cutoff>`** counts Warning events at or after an
  RFC3339 cutoff from `kubectl get events -o json` on stdin, and echoes
  `unknown` when jq could not parse the input. The counting rule is
  `WARNING_EVENTS_JQ`, a readable constant rather than a line inside a remote
  body; a consumer narrows it there.
- **Verdicts:** `classify_regular` / `regular_failing_predicates` (the console
  OK / PARTIAL / FAILED ladder, the second naming which predicates failed) and
  `classify_json` (healthy / degraded / catastrophic). The two read the same
  signals, so a signal added to one is added to the other.
- Function-only and safe to source under `set -e`. Requires `jq` only for
  `warning_events_filter`.

### `find-pve-host-for-vm.sh`

```
find-pve-host-for-vm.sh <vmid> <host1> [host2 ...]
```

Exit 0 with the host name on stdout; exit 1 with diagnostics on stderr.

### `resolve-tool.sh`

```
resolve-tool.sh <tool> [python-module]
```

Prints the invocation — possibly multi-word (`python3 -m molecule`) or an
absolute pyenv path — or exits 1. **Callers must invoke the result UNQUOTED** so
a multi-word form word-splits:

```bash
MOL=$(scripts/resolve-tool.sh molecule molecule) || exit 1
$MOL test
```

`find-pve-host-for-vm.sh` env: `PVE_NODE_PREFIX` (default `pve-`) — the prefix
this site's SSH targets carry that the node names the Proxmox API reports do
not. It is applied once to BOTH API-derived answers (`ha-manager status` and
`pvesh get /cluster/resources` report the same bare node identifier); the
per-host `qm status` scan is exempt, since that branch returns a target from the
caller's own list. Set it to `""` when the two already agree; leaving it at the
default on a site whose nodes are named otherwise returns a hostname that does
not resolve.

### `wait-for-reloader-roll.sh`

Waits for Reloader to pick up a patched ConfigMap and bump a Deployment's
generation. Takes the namespace, the deployment, the generation read **before**
the patch, then an optional timeout (60s) and a noun for the message.

```bash
gen=$(kubectl get deployment app -n ns -o jsonpath='{.metadata.generation}')
# patch the ConfigMap here
scripts/wait-for-reloader-roll.sh ns app "$gen" 60 provider
```

- **A generation that has not moved when the timeout expires is a FAILURE.** The
  caller's own `kubectl rollout status` would otherwise run against the
  unchanged generation and report success before the pod adopts the value.
- An unreadable generation reads as unchanged, so an API blip keeps polling and
  the deadline fails loudly instead of reporting a roll never observed.
- **Env:** `RELOADER_POLL_INTERVAL` (2) — the deadline is wall-clock, so `0`
  still terminates.
- The failure message's remedy deletes the pod rather than running `rollout
  restart`: the `restartedAt` annotation reads as drift to the
  kustomize-controller, which reverts it and undoes the restart.

---

## Shared assets outside `scripts/`

| Path | What it is |
|---|---|
| `kubernetes/reapers/kube_reaper.py` | the shared half of a pod-reaper CronJob program (kube-apiserver client, paging, age arithmetic, uid-preconditioned deletes, config validation, entry point). Stdlib only; mounted beside the app script so `import kube_reaper` resolves from the same directory |
| `tests/copier_render.py` | copier render harness for a template repo's own suite: `copy_source`, `copier_argv`, `render`, `cli_main`, `check_registered_copies`. Every per-repo value is a parameter |
| `lint/gitattributes` | the line-ending policy, vendored as a consumer's `.gitattributes` |

---

## Linters the family runs from a template, not a script

`tflint` has no script here: it ships as the CI template
[`ci/lint/terraform-tflint.yml`](INCLUDE-CONTRACT.md#cilintterraform-tflintyml),
which consumers include instead of carrying a local job. It catches deprecated
syntax, unused declarations and provider-specific rules that `terraform
validate` does not. Run it by hand against a module when a plan surprises you.

### `check-ci-include-job-names.py` (PyYAML)

Resolves every `needs: optional: true` entry to a job something actually
creates. GitLab ignores an optional need that names no job, so a library rename
or a non-default `job_name` input turns the dependency off with no error: the
gate the pipeline waits for stops blocking it.

```
scripts/check-ci-include-job-names.py --lib-path ../weisssrv-lib
```

- `--ci-file` (repeatable, default `.gitlab-ci.yml`), `--repo-root`,
  `--lib-path` (default `$WEISSSRV_LIB_PATH`).
- Job names and optional needs both come from each file's own keys and from
  every file it includes, nested includes included. A `local:` include resolves
  in the checkout its including file came from; a `project:` include resolves in
  the library checkout. A file already open on the same branch is not reopened,
  so an include cycle ends instead of looping.
- Every finding names the file and the job that declare the need, so a stale
  dependency inside a library template reads as that template's, not the
  pipeline's.
- `$[[ inputs.* ]]` resolves against the include's own `inputs:` first, then the
  included file's `spec.inputs` default. It resolves in a job key, in a need's
  `job:`, in an include's path and in the values an include passes on. A
  `needs:` that is one `$[[ inputs.needs ]]` takes the whole array from the
  include. A `.hidden` key creates no job, so a need on one is a finding.
- `--extra-job JOB=REASON` declares a job created by a source this gate cannot
  read (a `remote:`, `component:` or `template:` include). A reason is
  mandatory, and an entry no need references is a finding.
- `--require-optional-needs` makes a pipeline declaring no optional need an
  operator error, for a consumer that relies on them.
- **Vendoring:** imports `ci_yaml.py` from its own directory. Vendor the pair.
- **Forge: gitlab-only.** It resolves job names out of a GitLab CI file's keys,
  `local:`/`project:` includes and `spec.inputs` defaults, so an Actions
  pipeline replaces it rather than porting it.
- **Exit codes:** 0 clean, 1 on an unresolvable need or a stale `--extra-job`,
  2 on an operator error — a missing `--ci-file`, an unparseable file, or no job
  names resolved at all.

### `check-include-contract.py` (PyYAML)

Cross-checks a consumer pipeline against the input contract of the library
files it includes. The library owns that contract
([`INCLUDE-CONTRACT.md`](INCLUDE-CONTRACT.md)); `check-lib-pins.py` checks the
refs and `check-role-inputs.py` checks role variables, so this gate is what
catches an input typo or a dropped stage before a ref bump reaches pipeline
creation.

```
scripts/check-include-contract.py --lib-path ../weisssrv-lib
```

- `--ci-file` (repeatable, default `.gitlab-ci.yml`), `--repo-root`,
  `--lib-path` (default `$WEISSSRV_LIB_PATH`).
- **Three findings.** An `inputs:` key the included file's `spec.inputs` does
  not declare. A declared input with no `default:` that the include passes
  nothing for, which GitLab treats as REQUIRED and rejects the pipeline over. A
  job whose `stage:` resolves to a stage the pipeline's `stages:` list does not
  hold.
- Stages come from the pipeline's own `stages:`, else GitLab's
  `build, test, deploy`, always plus `.pre` and `.post`. A job with no `stage:`
  resolves to `test`; a job with `extends:` is left unchecked, since its stage
  may live in another file. A `.hidden` key creates no job, and `pages` is
  staged by GitLab itself.
- `$[[ inputs.* ]]` in a job name or a `stage:` resolves against what the
  include passes first, then the included file's own default.
- `local:` and `project:` includes are both read. A `remote:`, `component:` or
  `template:` include is printed as "not contract-checked" rather than skipped
  in silence, and a run where nothing was readable is an operator error.
- **Vendoring:** imports `ci_yaml.py` from its own directory. Vendor the pair.
  `tests/test_check_include_contract.py` is offered beside it.
- **Forge: gitlab-only.** It reads `inputs:`, `spec.inputs` and `stages:` out of
  GitLab CI YAML, so an Actions pipeline replaces it rather than porting it.
- **Exit codes:** 0 clean, 1 on a contract violation, 2 on an operator error —
  a missing `--ci-file` or included file, an unparseable file, a `project:`
  include with no library checkout, or nothing inspected.

### `check-helm-values-coverage.py` (PyYAML)

Holds the helm-values release registry to the HelmReleases on disk. The registry
drives `validate-helm-values.py`, so a release missing from it is rendered by
nothing and a `spec.values` typo there reaches the cluster.

```
scripts/check-helm-values-coverage.py --releases scripts/helm-values-releases.yaml
```

- `--repo-root`, `--releases` (default
  `<repo-root>/scripts/helm-values-releases.yaml`), `--manifest-dir`
  (repeatable, default `kubernetes`).
- Each `releases:` entry needs `name`, `manifest` and `chart`, an existing
  manifest holding a HelmRelease, and a `chart`/`repo_name` pair matching that
  manifest's own `spec.chart.spec`. A duplicate name or manifest is a finding.
- Coverage runs both ways: a HelmRelease neither listed nor in the `excluded:`
  map fails, an `excluded:` entry naming no HelmRelease is stale, an exclusion
  with no reason fails, and a path both listed and excluded fails.
- **Exit codes:** 0 clean, 1 on a finding, 2 on an operator error — an empty or
  malformed registry, a missing manifest directory, or no HelmRelease found.

### `check-prometheus-rule-coverage.py` (PyYAML)

Accounts for every shipped alert against the promtool unit tests, so the gap
between what fires in production and what is ever evaluated is recorded rather
than discovered during an incident.

```
scripts/check-prometheus-rule-coverage.py --tests-dir tests/prometheus-rules \
  --release kubernetes/infrastructure/observability/kube-prometheus-stack/release.yaml
```

- `--tests-dir` (required), `--repo-root`, `--manifest-dir` (repeatable,
  default `kubernetes`), `--loki-rules-dir` (repeatable).
- The corpus is the release's `additionalPrometheusRulesMap`, every
  PrometheusRule CR under the walked trees, and the bare `groups:` Loki ruler
  files. Pass exactly one of `--release <manifest>` or `--no-release`: an unset
  release would silently drop every inline rule group.
- A test counts an alert as covered when any `*.test.yaml` (or `.test.yaml.jinja`)
  under `--tests-dir` names it in an `alertname:` key.
- `--untested ALERTNAME=REASON` records a deliberate gap. An entry a test now
  covers, or one no rule declares, is a finding — as is a test naming an alert
  no rule declares, which passes against nothing.
- **Exit codes:** 0 clean, 1 on a finding, 2 on an operator error — an empty
  corpus, a missing tests directory, or a missing release manifest.

### `check-rule-windows.py` (PyYAML)

Fails an alert whose `for:` hold is at or past the lookback window its own
expression accumulates over. `count_over_time` and `increase` decay out of their
window, so the expression goes false on the evaluation where the hold would
elapse and a single burst never notifies.

```
scripts/check-rule-windows.py kubernetes/infrastructure/observability \
  --allow SomeAlert=the burst repeats until it is cleared
```

- Takes rule files, or directories searched for `*.yaml` and `*.yml`. Both
  PrometheusRule CRs and plain `groups:` ruler files are read.
- Only accumulating functions are checked: `count_over_time`,
  `sum_over_time`, `increase`, `delta`, `idelta`, `changes` and `resets`. A
  `rate` or an `avg_over_time` expresses a sustained condition, where a longer
  hold is the point.
- The tightest window in the expression bounds the hold. For a subquery
  `[range:step]` only the range counts, and quoted spans are skipped so a Loki
  line filter is not read as a range selector.
- `--allow ALERT=REASON` exempts an alert whose hold is deliberately at or past
  its window; repeatable. The reason is mandatory and printed.
- **Exit codes:** 0 clean, 1 on a hold that outlasts its window, 2 on an
  operator error — a missing path, an unparseable file or duration, an
  exemption with no reason, or a scan that examined no alert with both a hold
  and an accumulating lookback.
- **Vendoring:** offered in `scripts/vendorable-paths.yml`. Vendor
  `tests/test_check_rule_windows.py` with it to keep the negative cases.

### `check-secret-rotation-coverage.py` (PyYAML)

Fails an ESO-managed credential that no document says how to rotate. Both the
vault item titles (`remoteRef.key`, `extract`, `find`) and the ExternalSecrets
themselves are checked, so a write-capable token cannot ship with no rotation
path.

```
scripts/check-secret-rotation-coverage.py --doc docs/CREDENTIALS.md
```

- `--doc` (required, repeatable), `--repo-root`, `--manifest-dir` (repeatable,
  default `kubernetes`).
- A `ClusterExternalSecret` is keyed by the `spec.externalSecretName` it fans
  out, which is the name an operator rotates; a namespaced ExternalSecret is
  keyed `namespace/name`. A namespace-less ExternalSecret is reported, because
  coverage for one including namespace would silently stand for the rest.
- Matching is whole-name, so `alpha-secret-v2` in the document does not cover
  `alpha-secret`.
- `--declared-manual NAME=REASON` exempts a secret rotated outside the document.
  A reason is mandatory, and an entry no ExternalSecret declares is a finding.
- **Exit codes:** 0 clean, 1 on a finding, 2 on an operator error — a missing
  manifest directory, an unreadable document, or no ExternalSecret found.

### `supervised-apply-guard.sh`

Confirmation ceremony for an apply a human must watch. It refuses a non-tty, so
a scheduled pipeline cannot reach the prompt and answer it from a piped stdin;
it refuses every `-auto-approve` spelling; and it makes the operator type the
confirm word.

```
scripts/supervised-apply-guard.sh unifi:apply "the UniFi network" "$@"
```

- Takes the task name and what the apply rewrites, then the task's own CLI args
  so the `-auto-approve` scan sees them. Call it as the task's FIRST command, so
  `read` inherits the task's stdin.
- `SUPERVISED_APPLY_CONFIRM_WORD` (default `apply`) sets the word. An
  explicitly empty value is an operator error, because a bare Enter would
  then approve.
- **Exit codes:** 0 confirmed, 1 the operator declined, 2 a refusal — no
  terminal, `-auto-approve`, too few arguments, or an empty confirm word.

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
