# taskfiles/

go-task include fragments a consumer vendors so `task <x>` reproduces what the
matching CI template runs. They are part of the tag-versioned surface: vendor
them at the same library tag your `include:` block pins.

| File | Tasks | Mirrors |
|---|---|---|
| `lint.yml` | `yamllint`, `shellcheck`, `doc-links` (and `default` = all three) | `ci/lint/yaml-lint.yml`, `ci/lint/shellcheck.yml` (direct globs + optional `find_dir` recursion; NOT the `*.sh.j2` Jinja neutralizer, which stays CI-only), `ci/lint/docs-link-check.yml` |
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
consuming Taskfile — `SHELLCHECK_GLOBS`, `SHELLCHECK_FIND_DIR`, `YAMLLINT_ARGS`,
`DOC_LINK_SCRIPT`, `FLUX_PATH`, `K8S_VERSION`.

## Gating a vendored fragment

`scripts/check-taskfile.sh` verifies that every `scripts/` path a Taskfile
references exists, and it follows `includes:` — so pointing it at the root
Taskfile covers the fragments vendored here along with their own script
references (`lint.yml`'s `DOC_LINK_SCRIPT`):

```bash
scripts/check-taskfile.sh Taskfile.yml
```

It recognises both the `name: path.yml` shorthand and the
`name: {taskfile: path.yml}` map form under a column-0 `includes:` block,
resolved relative to the including file. A flow-style `includes:` mapping is not
matched — pass such a fragment as an extra argument (the command accepts
several) so the included half is not silently unguarded. A missing include
target is a failure, matching go-task, which fails at load time on one.

## Requirements

`yamllint`, `shellcheck`, `kustomize` and `kubeconform` on `PATH` — the same
tools (and pinned versions) the CI templates download. Tasks fail rather than
skip when a tool is missing; `doc-links` skips only when `DOC_LINK_SCRIPT` is
absent from the repo.
