# Include contract

How to consume each CI template, its `spec:inputs`, and the parity note that
records which weisssrv job(s) it reproduces. All templates are included the same
way — by project + pinned ref + file path, with optional `inputs:`:

```yaml
include:
  - project: eric/weisssrv-lib
    ref: v0.1.0            # a release TAG (see VERSIONING.md) — never a branch
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
  `extra_validation` (empty).
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

## ci/security/secret-detection.yml

- **Reproduces:** weisssrv `secret_detection`; template `secret_detection`.
- **Includes** GitLab's `Jobs/Secret-Detection.gitlab-ci.yml` and overrides the
  job. **Inputs:** `stage` (security), `cpu_selector` (`esweiss.com/cpu=modern`),
  `historic_scan` (false), `allow_failure` (true).
- **Parity:** rules (MR/main/schedule) + the node-selector pin are identical to
  both consumers. `allow_failure` defaults to `true` — the managed template's
  (and weisssrv's) effective value — so findings only warn; weisssrv gets its
  hard block from `validation-gate` needing this job non-optionally. A tenant
  without such a gate that wants findings to block a merge (e.g. a non-Ultimate
  tier where the security report isn't enforced) passes `allow_failure: false`.
  Pair with `lint/gitleaks.toml` + `lint/secret-detection-ruleset.toml` (vendored
  as `.gitleaks.toml` and `.gitlab/secret-detection-ruleset.toml`).

## ci/build/docker-build.yml

- **Reproduces:** the DinD `build_and_push` mechanics from weisssrv's
  `.build-molecule-base` (static docker CLI sha-pinned, dind wait, registry layer
  cache + inline cache, bounded retry, `:<sha>` always + `:latest` on main).
- **Inputs:** `job_name` (build-image), `image` (python:3.11), `tags`
  (**must be a privileged runner**), `dind_service` (docker:24.0-dind),
  `docker_cli_version` (27.5.1) + per-arch shas, `registry`
  (`$CI_REGISTRY_IMAGE`), `image_name` (empty → push to the registry base),
  `context` (.), `dockerfile` (empty → context Dockerfile), `extra_build_args`,
  `publish_on_main` (true), `cpu_selector`.
- **Note:** the shared tenant runner is non-privileged and CANNOT build — this
  template is weisssrv-only unless the consumer registers a privileged runner.
  weisssrv's molecule / hermes / camofox image builds stay in weisssrv (only the
  reusable pattern is extracted).
- **Tenant (with own privileged runner):** `inputs: { tags: [their-runner],
  context: ".", dockerfile: "Dockerfile" }`.

## ci/test/python-tests.yml

- **Reproduces:** weisssrv `python-tests`.
- **Inputs:** `test_dir` (`scripts/`), `pytest_version` (9.1.1), `pyyaml_version`
  (6.0.2), `apt_packages` (`git jq`), `changes`.
- **Parity:** junit report + before_script (apt + pinned pip) + rules are
  verbatim. The default `changes` is the generic subset (`scripts/**/*`,
  `.gitlab-ci.yml`); weisssrv's `.paths-python-tests` additionally guards four
  ansible paths (`all.yml`, `adguard_home/tasks/api_base_config.yml`,
  `ansible/roles/**/molecule/**/*`, `ansible/integration-tests/**/*`) whose
  pytest suite validates ansible tree files — out of library scope. **Weisssrv
  adopts by passing its full `.paths-python-tests` as `changes`**; defaults are
  NOT byte-identical for this job.

---

## Shared fragments (ci/templates/)

These define hidden jobs (no `spec:inputs`); `include` the file, then `extends`
or `!reference` the hidden job.

- **dep-cache.yml** → `.dep-cache` (pip + galaxy cache). `extends: .dep-cache`.
- **install-1password.yml** → `.install-1password` / `.install-1password-alpine`.
  `before_script: - !reference [.install-1password, before_script]`. Root runner
  only.
- **terraform-http-backend.yml** → `.terraform-http-backend` (GitLab HTTP state,
  default state `cloudflare`). `extends: .terraform-http-backend`; override
  `TF_HTTP_ADDRESS` per-job for other states.

## What is NOT here

Deliberately kept in weisssrv (see the extraction report): every `deploy-*` /
`maintenance-*` job, `validation-gate`, `test-aggregate-*`, the molecule +
integration matrices and `generate-molecule-pipeline.py` +
`.gitlab/ci/molecule-jobs.gitlab-ci.yml`, `repo-sync-checks`,
`repo-policy-checks`, `ansible-lint`, `prometheus-config-lint`, `version-check`,
the tailscale/authentik/b2 drift plans, and the hermes/camofox image builds.
`molecule-retry.sh` and `sanitize-junit-expected-failures.py` are molecule-only
and stay in weisssrv — no template-derived project runs molecule.
