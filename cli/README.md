# weisssrv-new-project

The scaffolding CLI for weisssrv cluster tenant repos. It turns a fresh copy of
[weisssrv-project-template](https://git.ericsweiss.com/eric/weisssrv-project-template)
into a configured project. Offline and dependency-light — the only runtime
dependency is `ruamel.yaml`, which handles both the round-trip edits that
preserve the scaffold's comments and the plain safe-loads used to inspect
documents. No PyYAML is needed at runtime (it is a test-only extra).

## Install / run

The distribution is `weisssrv-lib-cli`; the console script is
`weisssrv-new-project`. Pin the library tag — the CLI encodes the template
contract of the tag it ships in.

```bash
# No install (the path the template's scripts/rename.sh takes):
pipx run --spec 'git+https://git.ericsweiss.com/eric/weisssrv-lib.git@v0.2.0#subdirectory=cli' \
  weisssrv-new-project --help

# Install the console script. The spec is POSITIONAL: `pipx install --spec …`
# fails with "unrecognized arguments: --spec" (pipx dropped that flag).
pipx install 'git+https://git.ericsweiss.com/eric/weisssrv-lib.git@v0.2.0#subdirectory=cli'

# …with the copier extra, which only `new-cluster` needs:
pipx install 'weisssrv-lib-cli[cluster] @ git+https://git.ericsweiss.com/eric/weisssrv-lib.git@v0.2.0#subdirectory=cli'

# From a checkout of this library:
pip install ./cli            # or: pip install './cli[cluster]'
PYTHONPATH=cli python3 -m weisssrv_lib_cli --help   # no install at all
```

weisssrv-lib is an **internal-visibility** GitLab project, so `git+https://`
needs credentials (a PAT with `read_repository` in the URL, or use
`git+ssh://git@git.ericsweiss.com/eric/weisssrv-lib.git@v0.2.0#subdirectory=cli`).

## Commands

Run each from the project root, or pass `--root <dir>`.

### rename `<app-slug> <gitlab-group>`

Replaces the `changeme-app` / `changeme-group` placeholders across the tracked
tree. The slug must be a DNS label (it becomes the namespace and Flux
Kustomization name); the group is a GitLab namespace path (may be nested).

```bash
weisssrv-new-project rename recipe-box eric/apps
```

### prune `<feature...>`

Structurally removes components you don't need (deletes the manifest, drops its
kustomization entry, and cleans cross-references):

| feature            | effect |
|--------------------|--------|
| `secrets`          | delete externalsecret.yaml + the deployment secret env block |
| `metrics`          | delete servicemonitor.yaml + the observability-scrape NetworkPolicy |
| `pdb`              | delete pdb.yaml |
| `single-replica`   | delete pdb.yaml + set the deployment to `replicas: 1` |
| `hpa`              | delete the opt-in hpa.yaml |
| `external-ingress` | remove the public IngressRoute + Certificate (keep the internal variants) |
| `image-build`      | delete a repo-root Dockerfile / .dockerignore |
| `manifest:<file>`  | delete kubernetes/flux/&lt;file&gt;.yaml + its kustomization entry |

All requested features are validated up front — an unknown feature name (or an
`external-ingress` prune that would empty a file because no internal variant is
active) raises before any file is touched, so a typo never half-mutates the repo.

```bash
weisssrv-new-project prune metrics single-replica
# internal-only: wire the internal route first, then drop the public one
# (prune external-ingress refuses if the internal variant isn't active yet)
weisssrv-new-project wire internal-ingress && weisssrv-new-project prune external-ingress
```

### wire `<feature...>`

Enables opt-in components (uncomments the shipped-commented blocks and makes the
paired data edits):

| feature            | effect |
|--------------------|--------|
| `hpa`              | add hpa.yaml, uncomment the HPA, drop `replicas`, make the VPA memory-only |
| `internal-ingress` | activate the internal IngressRoute + Certificate (`*.esweiss.com`) |
| `sso`              | add the Authentik forward-auth middleware to the public route |

```bash
weisssrv-new-project wire hpa
```

### verify

Sanity-checks the result: no placeholder tokens remain, every kustomization
resource exists on disk, no manifest is orphaned, and (when `kustomize` is on
PATH) `kustomize build kubernetes/flux` succeeds. Exit non-zero on any hard
problem.

```bash
weisssrv-new-project verify          # runs kustomize build if available
weisssrv-new-project verify --no-kustomize
```

### new-cluster `<source> <destination>` — EXPERIMENTAL

Renders a **cluster** template (a [copier](https://copier.readthedocs.io)
template, unlike the fork-and-rename app scaffold above) into an absent-or-empty
directory. `weisssrv-cluster-template` is not published yet, so the flags may
still change. Needs the `cluster` extra.

| flag | effect |
|------|--------|
| `--vcs-ref REF` | render a tag/branch/commit (git sources only) |
| `--data KEY=VALUE` | answer one question non-interactively (repeatable) |
| `--defaults` | take the template default for every unanswered question |
| `--pretend` | render without writing anything |
| `--trust` | let the template run tasks / jinja extensions (copier `--trust`) |

```bash
weisssrv-new-project new-cluster \
  https://git.ericsweiss.com/eric/weisssrv-cluster-template.git ./my-cluster \
  --vcs-ref v0.1.0 --data cluster_name=lab --defaults

# Iterating on an unreleased template: a local path works as the source.
weisssrv-new-project new-cluster ../weisssrv-cluster-template ./my-cluster --defaults
```

Without `--defaults` (or a complete `--data` set) copier prompts interactively,
which fails on a non-TTY runner. For a **git** source with no `--vcs-ref`,
copier renders the template's latest *tag* — not its default branch.

## Tests

```bash
python3 -m pytest cli/tests -q
```

The suite copies the bundled scaffold fixture into a tmpdir per test and
exercises every command (including a full rename → prune → wire → verify flow),
mirroring the weisssrv `scripts:test` throwaway-tree pattern. No network, no
install required. `new-cluster` renders `tests/fixtures/copier-template`, a
miniature local copier template; those tests skip without the `cluster` extra.

`tests/test_template_contract.py` binds the bundled fixture to the real app
template: it asserts byte-equality for every file the CLI parses, that the
`kubernetes/flux` manifest sets match, and that both trees satisfy the layout
contract hardcoded in `tree`/`prune`/`wire` (opt-in lines, document names,
`-internal` suffix, the `authentik-auth` middleware pair, …). It uses a sibling
`weisssrv-app-template` / `weisssrv-project-template` checkout by default:

```bash
WEISSSRV_TEMPLATE_ROOT=~/src/weisssrv-app-template python3 -m pytest cli/tests -q
```

Without a checkout the template half skips — resync the fixture (the failure
message prints the `cp` command) whenever the template changes.
