# lint/

Shared linter configuration for the weisssrv family. These are **not** the
configs this repo lints itself with by reference — they are the source copies a
consumer **vendors**: copy the file into the consumer repo under the name that
tool expects, at the same library tag the consumer's `include:` block pins.

A CI template never reads a config out of this directory at job time. The
templates take a `config` input naming a path **in the consumer's tree**
(`-c .yamllint`, `--config lint/ruff.toml`), because the job checks out the
consumer, not the library. So a config here that a consumer has not vendored has
no effect on that consumer's pipeline.

| File | Vendor as | Consumed by |
|---|---|---|
| `yamllint-relaxed.yml` | `.yamllint` | `ci/lint/yaml-lint.yml` (`config: "-c .yamllint"`) |
| `yamllint-strict.yml` | `.yamllint` (alternative profile) | nothing today — vendor it and pass `-c .yamllint` if you want the stricter profile |
| `ruff.toml` | `ruff.toml` at the repo root, or kept at `lint/ruff.toml` | `ci/lint/python-lint.yml` (`config: "--config <path>"`) |
| `gitleaks.toml` | `.gitleaks.toml` at the repo root | `ci/security/secret-detection.yml`, via the ruleset below |
| `secret-detection-ruleset.toml` | `.gitlab/secret-detection-ruleset.toml` | GitLab's managed Secret-Detection job |
| `editorconfig` | `.editorconfig` | editors; nothing in CI reads it |
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

## Profiles

`yamllint-relaxed.yml` is the baseline syntax check a whole-tree `yaml-lint` job
runs: yamllint's shipped `relaxed` (comment rules disabled, line-length at
warning level), plus line-length off outright — inline documentation comments
run long — and `document-start` back on as a warning. It only applies where it
has been vendored: both templates pass `config: "-c .yamllint"`, while weisssrv
passes no `config` at all and so lints on the template default `-d relaxed`,
which is yamllint's own shipped profile rather than this file.

`yamllint-strict.yml` aligns with the ansible-lint production profile (160-char
limit, truthy/octal rules) and is meant to be applied **through** ansible-lint,
which honours `# noqa yaml[...]` exemptions that a bare `yamllint` run cannot.

`ruff.toml` selects `E4,E7,E9,F,W,B` — correctness rules only. Formatting rules
are deliberately excluded: the family runs no formatter, so they would bury the
findings that matter.

## Canonical source, and what "self-applied" means here

**This directory is the canonical copy of every profile below.** A fix goes in
here first; every root-level or `.gitlab/`-level file in any repo of the family
is a copy or a deliberate fork of one of these. Which is which is recorded in
[`../scripts/vendored-paths.yml`](../scripts/vendored-paths.yml) and checked by
`scripts/check-vendored-copies.py`: a `vendored` entry must stay
byte-identical, and a `forked` entry must still differ AND carry a
`reconciled_sha256` of the library side, so a change made here fails the fork
until someone absorbs it. The one exception is `.yamllint`: the copies in both
templates (root and `template/`) are unregistered, so a change to
`yamllint-relaxed.yml` reds nothing and must be propagated by hand until they
are registered. Register a new profile there in the same MR that adds it, or it
reaches no consumer.

The library's own application is uneven, which matters when judging whether a
change here has been exercised:

| File | How this repo applies it |
|---|---|
| `ruff.toml` | in place — `python-lint` passes `--config lint/ruff.toml` |
| `gitleaks.toml` | in place — `.gitlab/secret-detection-ruleset.toml` passes it through directly, with no root copy |
| `secret-detection-ruleset.toml` | forked to `.gitlab/`, differing only in the passthrough target above |
| `yamllint-relaxed.yml` | forked to `.yamllint`, which additionally disables `document-start` (a `spec:`-first CI template has no leading `---`) |
| `pre-commit-config.yaml` | forked to `.pre-commit-config.yaml`, which adds a local `check-doc-links` hook |
| `editorconfig` | forked to `.editorconfig`, prose differences only |
| `yamllint-strict.yml` | not applied here, and no consumer vendors it — an offered profile, exercised by nothing |

## Versioning

These files are part of the tag-versioned surface. A changed rule can turn a
consumer's green pipeline red on a bump with nothing in the consumer's own diff
to explain it, so a rule addition is treated as a behavior change under
[docs/VERSIONING.md](../docs/VERSIONING.md), and re-vendoring is part of the
upgrade procedure there.
