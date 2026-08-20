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

**Stability.** The CLI is covered by the library tag like everything else
(docs/VERSIONING.md): subcommand names, flags and exit codes are API, and a
removal or an incompatible change ships as a breaking release. Nothing here is
experimental. `copier copy` against the template URL stays an equally supported
path — this wrapper only adds the up-front source/destination validation.

## Install / run

The distribution is `weisssrv-lib-cli`; the console script is
`weisssrv-new-project`. Pin the library tag. The tags below are examples: use
the tag your repo pins (docs/VERSIONING.md).

```bash
# No install:
pipx run --spec 'git+https://git.ericsweiss.com/eric/weisssrv-lib.git@v0.11.1#subdirectory=cli' \
  weisssrv-new-project --help

# Install the console script. The spec is POSITIONAL: `pipx install --spec …`
# fails with "unrecognized arguments: --spec" (pipx dropped that flag).
pipx install 'git+https://git.ericsweiss.com/eric/weisssrv-lib.git@v0.11.1#subdirectory=cli'

# …with the copier extra, which rendering needs:
pipx install 'weisssrv-lib-cli[cluster] @ git+https://git.ericsweiss.com/eric/weisssrv-lib.git@v0.11.1#subdirectory=cli'

# From a checkout of this library:
pip install ./cli            # or: pip install './cli[cluster]'
PYTHONPATH=cli python3 -m weisssrv_lib_cli --help   # no install at all
```

weisssrv-lib is an **internal-visibility** GitLab project, so `git+https://`
needs credentials (a PAT with `read_repository` in the URL, or use
`git+ssh://git@git.ericsweiss.com/eric/weisssrv-lib.git@v0.11.1#subdirectory=cli`).

## new-cluster / new-app `[source] <destination>`

Both render a template into an absent-or-empty directory and take the same
flags; they differ only in the published template each one defaults to and names
in `--help` and in error messages. Omit `source` to render that published
template; pass one to render a fork, a mirror or a local checkout. Render one of
a template's tags with `--vcs-ref`. Needs the `cluster` extra (one extra, both
subcommands — it is just copier).

| flag | effect |
|------|--------|
| `--vcs-ref REF` | render a tag/branch/commit (git sources only) |
| `--data KEY=VALUE` | answer one question non-interactively (repeatable) |
| `--defaults` | take the template default for every unanswered question |
| `--pretend` | render without writing anything |
| `--trust` | let the template run tasks / jinja extensions (copier `--trust`) |

```bash
weisssrv-new-project new-cluster ./my-cluster \
  --vcs-ref v0.2.0 --data cluster_name=lab --defaults

weisssrv-new-project new-app ./recipe-box \
  --vcs-ref v0.1.0 --data app_slug=recipe-box --defaults

# Iterating on an unreleased template: a local path works as the source.
weisssrv-new-project new-cluster ../weisssrv-cluster-template ./my-cluster --defaults
```

| exit | meaning |
|------|---------|
| 0 | rendered (or, with `--pretend`, would render) |
| 1 | copier failed to render the template |
| 2 | bad arguments, or an unusable source/destination |
| 3 | copier is not installed — `pip install 'weisssrv-lib-cli[cluster]'` |

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

`requires-python = ">=3.9"` is what pip enforces on an installing machine. CI
runs the suite on one Python (the image's 3.13), so the floor is held here by
`tests/test_python_floor.py`, which parses every module and rejects both an
annotated module without `from __future__ import annotations` and any 3.10+
syntax. That covers
the constructs that actually break a 3.9 install; a 3.10-only stdlib call would
still get through, so a dependency-free package is part of the promise.
