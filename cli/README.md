# weisssrv-new-project

The copier wrapper for weisssrv templates. It renders a
[copier](https://copier.readthedocs.io) template into a new repo — validating
the source and destination up front, because copier's own failure modes are late
and messy. Two published templates, one subcommand each:

| subcommand | published template | renders |
|---|---|---|
| `new-cluster` | [weisssrv-cluster-template](https://git.ericsweiss.com/eric/weisssrv-cluster-template) | a whole cluster repo |
| `new-app` | [weisssrv-app-template](https://git.ericsweiss.com/eric/weisssrv-app-template) | a tenant repo that deploys INTO a cluster |

The package itself is stdlib-only and offline; copier is an optional extra.

## Install / run

The distribution is `weisssrv-lib-cli`; the console script is
`weisssrv-new-project`. Pin the library tag. The tags below are examples: use
the tag your repo pins (docs/VERSIONING.md).

```bash
# No install:
pipx run --spec 'git+https://git.ericsweiss.com/eric/weisssrv-lib.git@v0.7.2#subdirectory=cli' \
  weisssrv-new-project --help

# Install the console script. The spec is POSITIONAL: `pipx install --spec …`
# fails with "unrecognized arguments: --spec" (pipx dropped that flag).
pipx install 'git+https://git.ericsweiss.com/eric/weisssrv-lib.git@v0.7.2#subdirectory=cli'

# …with the copier extra, which rendering needs:
pipx install 'weisssrv-lib-cli[cluster] @ git+https://git.ericsweiss.com/eric/weisssrv-lib.git@v0.7.2#subdirectory=cli'

# From a checkout of this library:
pip install ./cli            # or: pip install './cli[cluster]'
PYTHONPATH=cli python3 -m weisssrv_lib_cli --help   # no install at all
```

weisssrv-lib is an **internal-visibility** GitLab project, so `git+https://`
needs credentials (a PAT with `read_repository` in the URL, or use
`git+ssh://git@git.ericsweiss.com/eric/weisssrv-lib.git@v0.7.2#subdirectory=cli`).

## new-cluster / new-app `<source> <destination>`

Both render a template into an absent-or-empty directory and take the same
flags; they differ only in the published template they name in `--help` and in
error messages. Render one of a template's tags with `--vcs-ref`. Needs the
`cluster` extra (one extra, both subcommands — it is just copier).

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
  --vcs-ref v0.2.0 --data cluster_name=lab --defaults

weisssrv-new-project new-app \
  https://git.ericsweiss.com/eric/weisssrv-app-template.git ./recipe-box \
  --vcs-ref v0.1.0 --data app_slug=recipe-box --defaults

# Iterating on an unreleased template: a local path works as the source.
weisssrv-new-project new-cluster ../weisssrv-cluster-template ./my-cluster --defaults
```

Without `--defaults` (or a complete `--data` set) copier prompts interactively,
which fails on a non-TTY runner. For a **git** source with no `--vcs-ref`,
copier renders the template's latest *tag* — not its default branch.

Any copier template works as a source, so either subcommand renders any of them
— or use `copier copy` directly, which is what the templates' own READMEs show.

## Tests

```bash
python3 -m pytest cli/tests -q
```

The suite renders `tests/fixtures/copier-template`, a miniature local copier
template, so it needs no network and no install. The render tests skip without
the `cluster` extra; everything else (argument parsing, source/destination
validation, the missing-copier hint) runs unconditionally.

## Python floor

`requires-python = ">=3.9"` is a compatibility promise enforced by lint
(`lint/ruff.toml` targets py39 and every module carries
`from __future__ import annotations`); CI itself only runs the suite on the
image's Python (3.13).
