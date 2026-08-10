# Versioning & tag pinning

## Consume by tag, never by branch

Every consumer pins a **release tag** in its `include: ref:`:

```yaml
include:
  - project: eric/weisssrv-lib
    ref: <CURRENT_TAG>
    file: /ci/lint/yaml-lint.yml
```

`<CURRENT_TAG>` is the placeholder convention used in every example across this
repo's docs. The literal current release is named once, in the
[README](../README.md#current-release) — substitute it when you copy a snippet.

A floating `main` (or `~latest`) ref would auto-propagate a library change into
every consumer's pipeline **with no review** — a supply-chain and
reproducibility hazard. Worse, a branch deleted after merge takes every include,
module source and collection install pinned to it. Pinning a tag makes every
pipeline reproducible and turns a library upgrade into an explicit, reviewable
bump.

## What a tag covers

One tag versions **everything** in the repo — CI templates, the
`weisssrv.infra` Ansible collection, `terraform/modules/`, `scripts/`,
`taskfiles/`, `lint/`, the published molecule images and the CLI — so a consumer
pins one ref and gets a self-consistent set. Consequences:

- The CLI distribution version (`cli/pyproject.toml`) mirrors the tag, and
  `weisssrv-new-project --version` reads it back from the installed
  distribution's metadata. A version of `0+source` means it is running off a
  checkout rather than an install. Bump it in the release MR, not after the tag.
- The collection version (`ansible_collections/weisssrv/infra/galaxy.yml`)
  mirrors the tag too — it is what `ansible-galaxy collection list` reports, so
  a stale value misreports what a host is running.
  `tests/test_ansible_collection.py` fails if it drifts from the CLI's, **and**
  if either drifts from the tag semantic-release would actually cut from this
  branch's commit subjects. Both files move in the same MR or the pipeline is
  red.
- A release cut for one area still moves every consumer's ref. That is fine —
  an unchanged component is byte-identical at the new tag.

`ci/internal/` is the one exception to all of it: those fragments are this
library's own pipeline wiring, not a consumer surface, and their inputs may
change in any release.

### Compatibility

There is no separate compatibility matrix to consult, and that is deliberate:
the tag *is* the compatibility statement. Everything released under one tag was
tested together in the pipeline that cut it — the templates against their own
jobs, the collection roles against the molecule images built from the same
commit, the scripts against `tests/`.

The one pair that needs an explicit act is the molecule images, because they
live in a registry rather than in git. The release pipeline's
`publish-molecule-image-tags` job runs after `semantic-release` and retags the
`molecule-ci` / `molecule-test` images this pipeline built to `:vX.Y.Z`,
appending that validated tag↔image pair to the registry. So a consumer that
pins `ref: vX.Y.Z` can pin `…/molecule-test:vX.Y.Z` and know the two were
exercised against each other. The job is a no-op when no release was cut, and
warns rather than reddening when the images were never pushed at all.

`:vX.Y.Z` image tags exist only from the first release that carried that job —
before it, pin `:latest` or an immutable `:<short-sha>`.

## No changelog file

This repo keeps **no** `CHANGELOG.md` and the collection keeps no
`changelogs/changelog.yaml`. Release notes are generated per tag by the release
job from the conventional commits in that release, and published on the GitLab
Release — one source, written by the thing that cut the tag. A hand-maintained
changelog alongside it went stale within a release and told consumers a
different story than the tag notes, so it was deleted and `changelogs` is
`build_ignore`d in `galaxy.yml` to keep a stray one out of the artifact.

To see what changed between two tags, read the releases, or
`git log v0.5.0..v0.5.2`.

## Release tags

- Tags are `vMAJOR.MINOR.PATCH` (semver), starting at **v0.1.0**.
- **MAJOR** — a breaking change to a template's inputs or behavior (a renamed or
  removed input, a changed default that alters a consumer's resolved pipeline).
  A script's CLI flags and config-file keys ([SCRIPTS.md](SCRIPTS.md)) count as
  inputs: renaming a flag or a config key, or changing where a script looks for
  its config, is MAJOR.
- **MINOR** — a new template, a new role, a new input with a back-compatible
  default, or a new script/CLI capability.
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
cut by the merge that earns it. An MR that changes the template renders it (an
undeclared input or malformed render fails pipeline creation); the job itself is
release-branch-only, so it first *runs* on the merge.

| commit subject | bump |
|---|---|
| `feat:` | MINOR |
| `fix:` / `perf:` / `refactor:` | PATCH |
| any `type!:`, or a `BREAKING CHANGE:` trailer | MAJOR — MINOR while 0.x, see below |
| `docs:` `ci:` `build:` `test:` `chore:` `style:` `revert:` | none — listed in the notes, never releases on its own |

No releasable commit means no release (exit 0), so re-running on an
already-released commit is a no-op. Because the bump comes from commit subjects,
a breaking change **must** be written as `feat!:` (or carry a `BREAKING CHANGE:`
trailer) or it ships as a patch. The corollary bites in the other direction too:
a release MR that hand-bumps `galaxy.yml` / `cli/pyproject.toml` to a MINOR while
carrying only `fix:`/`chore:` subjects fails
`tests/test_ansible_collection.py` — the declared version and the computed one
must agree.

While the current tag is **0.x**, a breaking commit bumps MINOR (0.2.0 → 0.3.0)
rather than cutting 1.0.0 — the pre-1.0 allowance above, and leaving initial
development stays a deliberate call rather than something a commit subject
triggers. The notes still lead with a "Breaking changes" section. Set the
template's `major_on_zero: true` input for the release that means 1.0.0.

### Prerequisite: protected tags

The release job creates the tag through the Releases API with `$CI_JOB_TOKEN`.
That works **only while `v*` is not a protected tag**, or while the project's
protected-tag rule allows the job token's effective permissions to create it. A
job token cannot create a tag that a protected-tag rule reserves for
Maintainers, and the failure is a 403 from the Releases call *after* the
pipeline has otherwise passed — the merge looks successful and no tag appears.

If `v*` is protected (or you protect it later), the job needs a token whose user
is allowed to create it:

```yaml
inputs:
  release_token: "$RELEASE_PAT"      # masked project/group variable
  token_header: "PRIVATE-TOKEN"
```

Protecting `v*` is worth doing — it stops anything but the release path from
minting a tag consumers pin — but it is a two-part change: protect the pattern
**and** switch the job to a PAT in the same MR, or the next merge silently stops
releasing.

## Upgrading a consumer

There is **no hosted Renovate** on this instance, so consumer bumps are manual —
or driven by `ci/maintenance/version-bump-bot.yml`, which opens one MR and never
merges it. That template is wired **by the consumer**, not here: it runs the
consumer's own version-check command against the consumer's own tracked-version
config, and this library tracks no upstream versions, so its pipeline does not
include it.

**A library bump is not just the `include: ref:`.** Five surfaces carry a pin,
and only the first is enforced by a gate. Walk all five in one MR:

| # | Surface | Where | Enforced by |
|---|---|---|---|
| 1 | CI template `ref:` on every include entry | `.gitlab-ci.yml` (plus the single-source `WEISSSRV_LIB_REF` variable) | `scripts/check-lib-pins.py` — vendored; fails on drift or a branch ref; `--fix` rewrites them |
| 2 | Ansible collection `version:` | `ansible/requirements.yml` | nothing — a stale pin installs an old collection silently |
| 3 | Terraform module `?ref=` | each `main.tf` `source =` | nothing — `terraform init` happily fetches the old tag |
| 4 | **Vendored scripts** | the consumer's `scripts/` | nothing — the copies drift and both keep working |
| 5 | CLI install spec | wherever `pipx install …@<tag>` is written (the app template's `scripts/rename.sh` has its own `LIB_REF` default) | nothing |

[CONSUMERS.yml](CONSUMERS.yml) records which of the five each consumer actually
has, and which files hold them — read it when cutting a release so no consumer
is missed. A generated cluster is the easy case: all of its pins derive from one
copier answer, `lib_ref`.

The procedure:

1. Read the target tag's GitLab release notes for what changed — the release job
   generates them per tag; this repo keeps no CHANGELOG file.
2. Bump every `ref:` / `version:` / `?ref=` in one MR (`check-lib-pins.py --fix`
   does surface 1).
3. **Re-vendor the scripts** the consumer copies. The include contract claims
   they are byte-identical to the library's; nothing checks that, so a bump that
   skips this leaves the consumer running last release's gate under this
   release's template.
4. If a template's inputs changed, update the consumer's `inputs:` accordingly —
   read the parity note per template in
   [INCLUDE-CONTRACT.md](INCLUDE-CONTRACT.md).
5. If the collection moved, work
   [MIGRATING.md](../ansible_collections/weisssrv/infra/MIGRATING.md) for the
   roles you consume, and land the inventory rename in the SAME MR as the
   collection bump — most renames have no back-compat shim.
6. For weisssrv specifically, prove pipeline parity before merging (merged-YAML
   diff + per-pipeline-type job-set enumeration) so no coverage is lost.

## Release checklist (this repo)

Before merging the MR that will cut a tag:

- [ ] `galaxy.yml` `version:` and `cli/pyproject.toml` `version:` both equal the
      version the commit subjects will produce (`tests/test_ansible_collection.py`
      asserts all three agree).
- [ ] The **Current release** line in `README.md` names the tag about to be cut.
      It is the only literal tag in the root README, `docs/` and the collection
      README — every pin example there is written as `<CURRENT_TAG>`. Sweep for
      regressions:

      ```bash
      grep -rn 'ref: v[0-9]\|@v[0-9]\|?ref=v[0-9]' \
        README.md docs/ ansible_collections/*/*/README.md
      ```

      A hit means a snippet re-hardcoded a tag and will rot.
- [ ] The READMEs that still carry a **literal** tag — `cli/README.md`,
      `docker/README.md`, and each `terraform/modules/*/README.md` — name the new
      one. These are per-directory front doors whose examples are copy-pasted
      directly, so they show a real tag rather than a placeholder; that means
      they need the bump by hand, and they are the ones that go stale.
- [ ] Every changed template's parity note in `INCLUDE-CONTRACT.md` reflects the
      change, and any new input is listed in that template's input set.
- [ ] `CONSUMERS.yml` still describes reality (a consumer that gained or dropped
      an include, a script it now vendors, a new pin site).
- [ ] A breaking change is written as `feat!:` or carries a `BREAKING CHANGE:`
      trailer — otherwise it ships as a patch and consumers get it unannounced.

## Pinned tool versions inside templates

Tool pins (kubeconform, kustomize, helm, docker CLI, buildx, yamllint, ruff,
ansible-lint, pytest, …) are **inputs** with defaults set to weisssrv's current
values. Bumping a tool is therefore either a library change (move the default +
re-verify the sha256, cut a MINOR/MAJOR tag) or a per-consumer override
(`inputs:`), never an unpinned moving target. Each downloaded binary is
sha256-verified before use; service and job images are digest-pinned where they
carry privilege (the DinD service, the AI-review image).

Two dependencies deliberately move outside this discipline, and both are
recorded where they bite: the GitLab-managed template nested by
`ci/security/secret-detection.yml` (no supported way to pin it — it tracks the
instance), and `ci/build/docker-build.yml`'s own `python:3.11` job image (a
version tag, not `:latest`; the checksum promise is scoped to downloaded
binaries).

## Terraform modules

Module sources pin the same tags, via the `?ref=` query on a `git::` source:

```hcl
source = "git::https://git.ericsweiss.com/eric/weisssrv-lib.git//terraform/modules/cloudflare-zone?ref=<CURRENT_TAG>"
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
    version: <CURRENT_TAG>
```

A role's variables are its inputs, so the template rules apply verbatim: a
renamed role, a renamed or removed role variable, and a changed default that
alters what a play does are all breaking. Three additions:

- **A shipped plugin is public API** — anything under `plugins/` is addressable
  as `weisssrv.infra.<name>` from any playbook, so renaming or removing one is
  breaking even if no role in the collection used it.
- **Raising the `meta/runtime.yml` `requires_ansible` floor is at least MINOR**
  — a consumer on an older ansible-core is refused the install.
- **A metric name a role emits is API too.** Alerts, promtool tests and
  dashboards bind to the literal string, and none of them live in this repo.
  Renaming one is breaking even though no variable changed.

Widening a `galaxy.yml` dependency to a new collection major is at least MINOR
too: the consumer resolves it against its own environment.

## CI Catalog (future)

The instance (GitLab 19.2) supports CI Catalog components, which would add a
versioned UI + `include: component:` semver resolution. This library starts with
plain tag-pinned `include: project:` for simplicity; promoting it to a catalog
resource is a later, additive step and does not change the pinning discipline
above.
