# taskfiles/

go-task include fragments a consumer vendors so `task <x>` reproduces what the
matching CI template runs. They are part of the tag-versioned surface: vendor
them at the same library tag your `include:` block pins.

| File | Tasks | Mirrors |
|---|---|---|
| `lint.yml` | `yamllint`, `shellcheck`, `doc-links` (and `default` = all three) | `ci/lint/yaml-lint.yml`, `ci/lint/shellcheck.yml`, `ci/lint/docs-link-check.yml` |
| `flux.yml` | `lint` (kustomize build + kubeconform), `render` | `ci/validate/flux-lint.yml` (SIMPLE mode, `substitute: false`) |

## Use

Vendor the file (e.g. into `.weisssrv-lib/taskfiles/`) and include it:

```yaml
includes:
  lint:
    taskfile: .weisssrv-lib/taskfiles/lint.yml
  flux:
    taskfile: .weisssrv-lib/taskfiles/flux.yml
```

Then `task lint`, `task flux:lint`. Override the fragment's `vars:` from the
consuming Taskfile — `SHELLCHECK_GLOBS`, `YAMLLINT_ARGS`, `DOC_LINK_SCRIPT`,
`FLUX_PATH`, `K8S_VERSION`.

## Requirements

`yamllint`, `shellcheck`, `kustomize` and `kubeconform` on `PATH` — the same
tools (and pinned versions) the CI templates download. Tasks fail rather than
skip when a tool is missing; `doc-links` skips only when `DOC_LINK_SCRIPT` is
absent from the repo.
