# AGENTS.md

Guidance for agents working in **weisssrv-lib** — the shared CI/tooling library
consumed by weisssrv and its cluster tenant repos.

## What this repo is

A single source of truth for the GENERIC CI/tooling layer both a homelab
platform repo and its tenants share: GitLab CI templates (`ci/`, with
`spec:inputs`), shared lint configs (`lint/`), stdlib helper scripts
(`scripts/`), go-task fragments (`taskfiles/`), and the tenant scaffolding CLI
(`cli/`). Consumers pull these in at a **pinned tag**.

Start with [README.md](README.md), then
[docs/INCLUDE-CONTRACT.md](docs/INCLUDE-CONTRACT.md) and
[docs/VERSIONING.md](docs/VERSIONING.md).

## Golden rules

- **Never push to `main`.** Every change ships via a feature branch + merge
  request. Do not tag — releases are cut deliberately (see VERSIONING).
- **No secrets in git**, ever. Templates reference `op://` / CI variables; they
  never embed credentials.
- **No AI/Claude attribution** anywhere (commits, MR text, code, docs). Commit
  style: `type(scope): summary`.
- **No Renovate** anywhere — the whole family bumps versions manually.
- **Preserve consumer parity.** The CI templates' input DEFAULTS reproduce
  weisssrv's current values; changing a default is a behavior change for
  weisssrv and must be flagged (and may need a MAJOR tag). When you change a
  template, update its parity note in the include contract.

## Local gates (run before opening an MR)

```bash
python3 -m pytest tests cli/tests -q                       # scripts + CLI tests
yamllint -c .yamllint ci/ lint/ taskfiles/ .gitlab-ci.yml
shellcheck --severity=warning --exclude=SC1091,SC2034 scripts/*.sh
ruff check --config lint/ruff.toml scripts tests cli
gitleaks detect --no-git --config lint/gitleaks.toml
python3 -c "import glob,yaml; [list(yaml.safe_load_all(open(f))) for f in glob.glob('ci/**/*.yml',recursive=True)]"
```

The library's own `.gitlab-ci.yml` runs the same set in CI, by including its own
templates (`include: local:`) rather than hand-rolling equivalent jobs — so a
template change is rendered and executed by the MR that makes it. Keep it that
way: a new job here should be a new template plus an include, not an inline job.

## Editing CI templates

- Templates are GitLab `spec:inputs` files: a `spec:` header document, then
  `---`, then the config that interpolates `$[[ inputs.<name> ]]`. They must
  begin with `spec:` (no leading `---`) — that is why `.yamllint` disables
  `document-start`.
- Interpolate an **array** input only in value position (`tags: $[[ inputs.tags
  ]]`), never mid-string.
- Keep the non-root vs root runner split intact (e.g. `flux-lint` branches on
  `substitute`): a job that assumes root (`apt-get`, `/usr/local/bin`) breaks on
  the shared tenant runner (non-privileged, UID 1000).
