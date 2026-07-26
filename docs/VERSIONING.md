# Versioning & tag pinning

## Consume by tag, never by branch

Every consumer pins a **release tag** in its `include: ref:`:

```yaml
include:
  - project: eric/weisssrv-lib
    ref: v0.2.0
    file: /ci/lint/yaml-lint.yml
```

A floating `main` (or `~latest`) ref would auto-propagate a library change into
both weisssrv's and every tenant's pipeline **with no review** — a supply-chain
and reproducibility hazard. Pinning a tag makes every pipeline reproducible and
turns a library upgrade into an explicit, reviewable bump.

## What a tag covers

One tag versions **everything** in the repo — CI templates, the
`weisssrv.infra` Ansible collection, `terraform/modules/`, `scripts/`,
`taskfiles/`, `lint/` and the CLI — so a consumer pins one ref and gets a
self-consistent set. Two consequences:

- The CLI distribution version (`cli/pyproject.toml`) mirrors the tag, and
  `weisssrv-new-project --version` reads it back from the installed
  distribution's metadata (`0.2.0+source` means it is running off a checkout,
  not an install). Bump it in the release MR, not after the tag.
- The collection version (`ansible_collections/weisssrv/infra/galaxy.yml`)
  mirrors the tag too — it is what `ansible-galaxy collection list` reports, so
  a stale value misreports what a host is running. `tests/test_ansible_collection.py`
  fails if it drifts from the CLI's.
- A release cut for one area still moves every consumer's ref. That is fine —
  an unchanged component is byte-identical at the new tag.

`ci/internal/` is the one exception to all of it: those fragments are this
library's own pipeline wiring, not a consumer surface, and their inputs may
change in any release.

## Release tags

- Tags are `vMAJOR.MINOR.PATCH` (semver), starting at **v0.1.0**.
- **MAJOR** — a breaking change to a template's inputs or behavior (a renamed or
  removed input, a changed default that alters a consumer's resolved pipeline).
  A script's CLI flags and config-file keys ([SCRIPTS.md](SCRIPTS.md)) count as
  inputs: renaming a flag or a config key, or changing where a script looks for
  its config, is MAJOR.
- **MINOR** — a new template, a new input with a back-compatible default, or a
  new script/CLI capability.
- **PATCH** — a fix that does not change any documented input or default.

While the library is **0.x**, a MINOR bump may carry a breaking input or default
change (semver's pre-1.0 allowance). Those are called out in the tag notes and in
the affected template's parity note in the include contract; a consumer that
pinned an older tag is unaffected until it bumps, so the breakage surfaces in the
bump MR — read the parity notes before merging one.

The tag is immutable — never move a published tag (a consumer that pinned it
would silently get different CI).

## Releases are cut automatically (conventional commits)

Merging to `main` runs `ci/release/semantic-release.yml`
(`scripts/semantic-release.py`): it reads the conventional commits since the
last tag, decides the bump, and creates the tag **and** the GitLab Release with
generated notes in one Releases-API call. This library wires that template into
its own pipeline — `.gitlab-ci.yml` declares `release` as the LAST stage and
self-includes the template with `tags: []` — so the tag every consumer pins is
cut by the merge that earns it, and the template is exercised by the MR that
changes it. A consumer that wants the same behaviour wires it the same way.

| commit subject | bump |
|---|---|
| `feat:` | MINOR |
| `fix:` / `perf:` / `refactor:` | PATCH |
| any `type!:`, or a `BREAKING CHANGE:` trailer | MAJOR — MINOR while 0.x, see below |
| `docs:` `ci:` `build:` `test:` `chore:` `style:` `revert:` | none — listed in the notes, never releases on its own |

No releasable commit means no release (exit 0), so re-running on an
already-released commit is a no-op. Because the bump comes from commit subjects,
a breaking change **must** be written as `feat!:` (or carry a `BREAKING CHANGE:`
trailer) or it ships as a patch.

While the current tag is **0.x**, a breaking commit bumps MINOR (0.2.0 → 0.3.0)
rather than cutting 1.0.0 — the pre-1.0 allowance above, and leaving initial
development stays a deliberate call rather than something a commit subject
triggers. The notes still lead with a "Breaking changes" section. Set the
template's `major_on_zero: true` input for the release that means 1.0.0.

## Upgrading a consumer

There is **no hosted Renovate** on this instance, so consumer bumps are manual
(mirroring weisssrv's `task maintenance:check-versions` discipline) — or driven
by `ci/maintenance/version-bump-bot.yml`, which opens one MR and never merges it.
That template is wired **by the consumer**, not here: it runs the consumer's own
version-check command against the consumer's own tracked-version config, and
this library tracks no upstream versions, so its pipeline does not include it
(it is the one template with no library-side workload besides `flux-lint` and
the `ci/templates/` fragments).

1. Read the target tag's GitLab release notes for what changed — the release job
   generates them per tag; this repo keeps no CHANGELOG file.
2. In the consumer, bump every `ref:` to the new tag in one MR.
3. If a template's inputs changed, update the consumer's `inputs:` accordingly.
4. For weisssrv specifically, prove pipeline parity before merging (merged-YAML
   diff + per-pipeline-type job-set enumeration) so no coverage is lost.

## Pinned tool versions inside templates

Tool pins (kubeconform, kustomize, helm, docker CLI, yamllint, pytest, …) are
**inputs** with defaults set to weisssrv's current values. Bumping a tool is
therefore either a library change (move the default + re-verify the sha256, cut
a MINOR/MAJOR tag) or a per-consumer override (`inputs:`), never an unpinned
moving target. Each downloaded binary is sha256-verified before use.

## Terraform modules

Module sources pin the same tags, via the `?ref=` query on a `git::` source:

```hcl
source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/cloudflare-zone?ref=v0.2.0"
```

Semver for a module follows the template rules, with one addition: **a change to
a resource address is MAJOR**, even when every input keeps its name and meaning.
Renaming a resource, splitting one into several, or changing what `for_each`
iterates makes the consumer's next plan a destroy + create of live
infrastructure unless they run `terraform state mv` (or the release ships
`moved {}` blocks). Call out the required state migration in the release notes.

Provider constraints inside a module are part of its contract too: the consumer
resolves them against its own lockfile, so widening or moving a constraint is at
least MINOR, and moving to a provider major that renames resources is MAJOR.

Modules do not commit a `.terraform.lock.hcl` — the lockfile belongs to the
consuming root module, which is what pins exact provider builds and hashes.

## Ansible collection

`requirements.yml` pins the same tags, on a git source:

```yaml
collections:
  - name: git+https://git.ericsweiss.com/eric/weisssrv-lib.git#/ansible_collections/weisssrv/infra
    type: git
    version: v0.2.0
```

A role's variables are its inputs, so the template rules apply verbatim: a
renamed role, a renamed or removed role variable, and a changed default that
alters what a play does are all breaking. Two additions:

- **A shipped plugin is public API** — anything under `plugins/` is addressable
  as `weisssrv.infra.<name>` from any playbook, so renaming or removing one is
  breaking even if no role in the collection used it.
- **Raising the `meta/runtime.yml` `requires_ansible` floor is at least MINOR**
  — a consumer on an older ansible-core is refused the install.

Widening a `galaxy.yml` dependency to a new collection major is at least MINOR
too: the consumer resolves it against its own environment.

## CI Catalog (future)

The instance (GitLab 19.2) supports CI Catalog components, which would add a
versioned UI + `include: component:` semver resolution. This library starts with
plain tag-pinned `include: project:` for simplicity; promoting it to a catalog
resource is a later, additive step and does not change the pinning discipline
above.
