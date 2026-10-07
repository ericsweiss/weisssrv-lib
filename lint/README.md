# lint/

Shared linter configuration for the weisssrv family. Most of these are the
source copies a consumer **vendors**: copy the file into the consumer repo under
the name that tool expects, at the same library tag the consumer's `include:`
block pins. The exception is `yamllint-self.yml`, which this repo lints itself
with and no consumer takes.

A CI template never reads a config out of this directory at job time. The
templates take a `config` input naming a path **in the consumer's tree**
(`-c lint/yamllint-relaxed.yml`, `--config lint/ruff.toml`), because the job
checks out the consumer, not the library. So a config here that a consumer has
not vendored has no effect on that consumer's pipeline.

| File | Vendor as | Consumed by |
|---|---|---|
| `yamllint-relaxed.yml` | `lint/yamllint-relaxed.yml` — **never** a root `.yamllint` | `ci/lint/yaml-lint.yml` (`config: "-c lint/yamllint-relaxed.yml"`) |
| `ruff.toml` | `ruff.toml` at the repo root, or kept at `lint/ruff.toml` | `ci/lint/python-lint.yml` (`config: "--config <path>"`) |
| `gitleaks.toml` | `.gitleaks.toml` at the repo root | `ci/security/secret-detection.yml`, via the ruleset below |
| `secret-detection-ruleset.toml` | `.gitlab/secret-detection-ruleset.toml` | GitLab's managed Secret-Detection job |
| `editorconfig` | `.editorconfig` | editors; nothing in CI reads it |
| `gitattributes` | `.gitattributes` | git checkin normalization; nothing in CI reads it |
| `pre-commit-config.yaml` | `.pre-commit-config.yaml` | `pre-commit install`, locally |

Two pairs must be vendored together or they do nothing:

- **`gitleaks.toml` + `secret-detection-ruleset.toml`.** GitLab's Secret
  Detection runs gitleaks under the hood, but a bare `.gitleaks.toml` at the
  repo root is ignored — `SECRET_DETECTION_RULESET_PATH` is not a supported
  variable. The ruleset file at `.gitlab/secret-detection-ruleset.toml` is what
  points the analyzer at your gitleaks config. Vendoring only the first gives a
  scan with none of your allowlist entries, which now **fails** the job
  (`allow_failure: false`).
  `secret-detection-ruleset.toml` is the only forge-coupled file in this
  directory: it exists to configure GitLab's managed analyzer. A GitHub
  consumer vendors `gitleaks.toml` alone and runs gitleaks itself; every other
  config here is tool config and is forge-neutral.
- **`ruff.toml` + `ci/lint/python-lint.yml`'s `config` input.** With an empty
  `config` ruff uses its own discovery, which will not find a file at
  `lint/ruff.toml` — pass the full argument or put the file where ruff looks.

**Do not vendor `yamllint-relaxed.yml` as a repo-root `.yamllint`.** ansible-lint
auto-discovers `.yamllint`, `.yamllint.yaml` and `.yamllint.yml` in the directory
it runs from and merges that file over its own `yaml[*]` rules, so a profile
there decides the levels ansible-lint reports yaml findings at. Keep the profile
at a path nothing discovers and pass `-c <path>` to every caller: the `yaml-lint`
job, the pre-commit hook and the Taskfile lint task. Every repo in the family
keeps it at `lint/yamllint-relaxed.yml`; this library keeps its own fork at
`lint/yamllint-self.yml`.

Both profiles are nonetheless **ansible-lint compatible**. A discovered config
that is not makes ansible-lint print `Found incompatible custom yamllint
configuration` and disable `--fix` for the whole run. Complying costs two rule
blocks, so the profiles comply.

Six settings have to hold in the merged config:
`comments.min-spaces-from-content: 1`, `comments-indentation` disabled,
`braces.min-spaces-inside: 0`, `braces.max-spaces-inside: 1`, and both
`octal-values.forbid-*` settings. `relaxed` supplies the `braces` and
`comments-indentation` values. `comments` and `octal-values` are set explicitly
here. `tests/test_lint_profiles.py` checks all six against the list in
ansible-lint's own `load_yamllint_config`.

## Profiles

`yamllint-relaxed.yml` is the baseline syntax check a whole-tree `yaml-lint` job
runs. It starts from yamllint's shipped `relaxed`, which keeps `line-length` at
warning level, disables the two comment rules (`comments`,
`comments-indentation`) and drops most style rules to warnings. On top of that:

- `line-length` off outright, because inline documentation comments run long.
- `document-start` back on as a warning.
- `comments` re-enabled at `min-spaces-from-content: 1`, and `octal-values`
  with both `forbid-*` settings on. Both sit at **warning** level, so adopting
  a newer library tag cannot red a pipeline over existing files; a consumer
  that has swept its tree can raise them to error locally.
- `empty-lines: max 1`, also a warning: one blank line between blocks.
- An `ignore` block for `.terraform/` and `terraform/**/.terraform/`, so one
  target list works in CI and in a local tree where `terraform init` has run.
  Without it every caller has to prune provider checkouts itself, and the local
  and CI target lists drift apart.

Warning-level rules do not fail a plain `yamllint` run, which is what keeps a
re-vendor safe. They still show in the output, and ansible-lint reports them as
`yaml[*]` findings wherever it picks the config up.

The profile only applies where it has been vendored: every consumer passes
`config: "-c lint/yamllint-relaxed.yml"`. Leave `config` unset and the job falls
back to the template default `-d relaxed`, which is yamllint's own shipped
profile rather than this file.

`ruff.toml` selects `E4,E7,E9,F,W,B`; the rationale for that selection lives in
the file's own header.

## Local gate hooks

`pre-commit-config.yaml` carries one `repo: local` block. It holds `check-taskfile`,
which runs `scripts/check-taskfile.sh` and is keyed on
`^(Taskfile\.yml|taskfiles/.*\.ya?ml)$` so a commit that touches only an
included taskfile still runs it. A consumer with no Taskfile drops the hook in
its fork.

Add the repo's own tool-light gates to that block, one hook per gate, in the
same shape, so every gate that CI runs also runs before the commit:

```yaml
      - id: kustomization
        name: kustomization resource list
        entry: python scripts/check-kustomization.py kubernetes/flux
        language: python
        additional_dependencies: ["pyyaml==6.0.2"]
        pass_filenames: false
        always_run: true
        files: ^(kubernetes/|scripts/check-)
```

`language: python` with a pinned `additional_dependencies` keeps the hook
working on a machine without PyYAML; `language: system` suits a gate that needs
only bash or the Python standard library. `files:` covers both the manifests and
the gate script, so editing the gate re-runs it. Heavy, tool-dependent gates
(molecule, promtool, a cluster-side render) stay CI-only.

## Canonical source, and what "self-applied" means here

**This directory is the canonical copy of every profile below.** A fix goes in
here first; every root-level or `.gitlab/`-level file in any repo of the family
is a copy or a deliberate fork of one of these. Which is which is recorded in
each consumer's own `scripts/vendored-manifest.yml` (these profiles are on the
offer list, [`../scripts/vendorable-paths.yml`](../scripts/vendorable-paths.yml))
and checked by `scripts/check-vendored-copies.py`: a `vendored` entry must stay
byte-identical, and a `forked` entry must still differ AND carry a
`reconciled_sha256` of the library side, so a change made here fails the fork
until someone absorbs it. The one exception is `yamllint-relaxed.yml`: the copies
in both templates (root and `template/`) are unregistered, so a change to it reds
nothing and must be propagated by hand until they are registered. Register a new
profile there in the same MR that adds it, or it reaches no consumer.

The library's own application is uneven, which matters when judging whether a
change here has been exercised:

| File | How this repo applies it |
|---|---|
| `ruff.toml` | in place — `python-lint` passes `--config lint/ruff.toml` |
| `gitleaks.toml` | in place — `.gitlab/secret-detection-ruleset.toml` passes it through directly, with no root copy |
| `secret-detection-ruleset.toml` | forked to `.gitlab/`, differing only in the passthrough target above |
| `yamllint-relaxed.yml` | forked to `lint/yamllint-self.yml`, which disables `document-start` (a `spec:`-first CI template has no leading `---`) and ignores the local collection cache |
| `pre-commit-config.yaml` | forked to `.pre-commit-config.yaml`, which swaps the profile path, drops `check-taskfile` (no Taskfile here) and adds `check-doc-links` |
| `editorconfig` | forked to `.editorconfig`, prose differences only |
| `gitattributes` | in place — vendored byte-identically as the repo-root `.gitattributes` |

## Versioning

These files are part of the tag-versioned surface. A changed rule can turn a
consumer's green pipeline red on a bump with nothing in the consumer's own diff
to explain it, so a rule addition is treated as a behavior change under
[docs/VERSIONING.md](../docs/VERSIONING.md), and re-vendoring is part of the
upgrade procedure there.
