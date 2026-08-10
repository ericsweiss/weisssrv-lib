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
| `yamllint-strict.yml` | `.yamllint` (alternative profile) | `ci/lint/ansible-lint.yml`'s embedded yaml rules |
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
runs: line-length off (inline documentation comments run long), comment rules as
warnings. `yamllint-strict.yml` aligns with the ansible-lint production profile
(160-char limit, truthy/octal rules) and is meant to be applied **through**
ansible-lint, which honours `# noqa yaml[...]` exemptions that a bare `yamllint`
run cannot.

`ruff.toml` selects `E4,E7,E9,F,W,B` — correctness rules only. Formatting rules
are deliberately excluded: the family runs no formatter, so they would bury the
findings that matter.

## Versioning

These files are part of the tag-versioned surface. A changed rule can turn a
consumer's green pipeline red on a bump with nothing in the consumer's own diff
to explain it, so a rule addition is treated as a behavior change under
[docs/VERSIONING.md](../docs/VERSIONING.md), and re-vendoring is part of the
upgrade procedure there. This library self-applies `ruff.toml`, both yamllint
profiles, `pre-commit-config.yaml` and `editorconfig`, so a change here is
exercised by the MR that makes it.
