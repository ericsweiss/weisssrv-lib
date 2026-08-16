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

The pairs that need an explicit act are the published images, because they live
in a registry rather than in git. The release pipeline's `publish-image-tags`
job runs after `semantic-release` and retags the `molecule-ci` /
`molecule-test` / `ansible-deploy` images this pipeline built to `:vX.Y.Z`,
appending that tag↔image pair to the registry. The two molecule images are
*exercised* by the pipeline that cuts the tag, so a consumer that pins
`ref: vX.Y.Z` can pin `…/molecule-test:vX.Y.Z` and know the two were run against
each other. `ansible-deploy` is retagged for pin alignment only — no job in this
pipeline runs it; what holds it to the tag is `tests/test_ansible_deploy_image.py`,
a static assertion that `docker/ansible-deploy/requirements.txt`'s `ansible==`
pin equals the `ansible_version` default in `ci/deploy/deploy-base.yml`. The job
is a no-op when no release was cut, and warns rather than reddening when an
image was never pushed at all.

`:vX.Y.Z` image tags exist only from the first release that carried that job,
and `ansible-deploy:vX.Y.Z` only from the release that added that image —
before either, pin `:latest` or an immutable `:<short-sha>`.

## No changelog file

This repo keeps **no hand-maintained changelog** and the collection keeps no
`changelogs/changelog.yaml`. Release notes are generated per tag by the release
job from the conventional commits in that release, and published on the GitLab
Release — one source, written by the thing that cut the tag. A second,
hand-written changelog goes stale within a release and tells consumers a
different story than the tag notes, so `changelogs` is `build_ignore`d in
`galaxy.yml` to keep a stray one out of the artifact. The collection's
`CHANGELOG.md` is a six-line pointer to that Releases page, not a changelog.

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

The behaviour comes from one file, the vendored `scripts/semantic-release.py`,
so **this table is the canonical copy**. A consumer doc states only the
consequence local to that repo and links here for the mapping; restating the
table downstream is how the pre-1.0 row loses its `see below`.

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
and three of them are enforced by a gate (1, 2 and 4). Walk all five in one MR:

| # | Surface | Where | Enforced by |
|---|---|---|---|
| 1 | CI template `ref:` on every include entry | `.gitlab-ci.yml` (plus the single-source `WEISSSRV_LIB_REF` variable) | `scripts/check-lib-pins.py` — vendored; fails on drift or a branch ref; `--fix` rewrites them |
| 2 | Ansible collection `version:` | `ansible/requirements.yml` | the same `scripts/check-lib-pins.py` — it reads the sibling `ansible/requirements.yml` and holds the collection `version:` equal to `WEISSSRV_LIB_REF`; `--fix` syncs it. The file it inspects is forge-independent, but the ref it compares against comes from `.gitlab-ci.yml`, so a consumer without one cannot run the gate |
| 3 | Terraform module `?ref=` | each `main.tf` `source =` | nothing — `terraform init` happily fetches the old tag |
| 4 | **Vendored scripts** (and the lint profiles, vendored suites and workflows alongside them) | the consumer's `scripts/`, plus the paths `scripts/vendored-paths.yml` records | `scripts/check-vendored-copies.py` — registry-driven, run from the consumer against a library checkout at its pinned `--ref`; fails on drift in either direction, on a converged fork, and on a fork whose library side moved since its `reconciled_sha256`. Per-consumer adoption is still landing |
| 5 | CLI install spec | wherever `pipx install …@<tag>` is written | nothing |

[CONSUMERS.yml](CONSUMERS.yml) records which of the five each consumer actually
has, and which files hold them — read it when cutting a release so no consumer
is missed. A generated cluster is the easy case: all of its pins derive from one
copier answer, `lib_ref`.

The procedure:

1. Read the target tag's GitLab release notes for what changed — the release job
   generates them per tag; this repo keeps no CHANGELOG file.
2. Bump every `ref:` / `version:` / `?ref=` in one MR (`check-lib-pins.py --fix`
   does surfaces 1 and 2).
3. **Re-vendor the scripts** the consumer copies. All three consumers gate
   byte-identity against the library at their pinned ref (see "A vendored
   script is a pin too" in [INCLUDE-CONTRACT.md](INCLUDE-CONTRACT.md)), so
   skipping this fails their pipeline rather than quietly leaving them on last
   release's gate under this release's template.
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

- [ ] `galaxy.yml` `version:`, `cli/pyproject.toml` `version:` and the
      **Current release** line in `README.md` all move TOGETHER to the version
      the commit subjects will produce (`tests/test_ansible_collection.py`
      asserts all three agree, so they stay green while all three name the
      *previous* release — nothing reds until they diverge).
- [ ] That **Current release** line is the ONLY literal tag in the root README,
      `docs/` and the collection README — every pin example there is written as
      `<CURRENT_TAG>`. Sweep for regressions:

      ```bash
      grep -rn 'ref: v[0-9]\|@v[0-9]\|?ref=v[0-9]' \
        README.md docs/ ansible_collections/*/*/README.md
      ```

      A hit means a snippet re-hardcoded a tag and will rot.
- [ ] The READMEs that still carry a **literal** tag — `cli/README.md`,
      `docker/README.md`, and each `terraform/modules/*/README.md` — name the new
      one. These are per-directory front doors whose examples are copy-pasted
      directly, so they show a real tag rather than a placeholder.
      `tests/test_release_version.py` holds them equal to `cli/pyproject.toml`
      and to the README **Current release** line, so a missed sweep reds the
      pipeline instead of shipping.
- [ ] Every changed template's parity note in `INCLUDE-CONTRACT.md` reflects the
      change, and any new input is listed in that template's input set.
- [ ] `CONSUMERS.yml` still describes reality (a consumer that gained or dropped
      an include, a script it now vendors, a new pin site). Its `enforced:`
      claims are mechanical —
      `tests/test_docs_registry.py::TestConsumersRegistry` fails a named gate
      that does not exist, checking the consumer-side ones whenever a sibling
      checkout is present. The prose around them is not.
- [ ] `scripts/vendored-paths.yml` is re-taken against the tree being tagged:
      every `reconciled_sha256` is the sha of the library file **as this release
      ships it**, and every `consumer:` path matches **the layout the consumer
      will be on when it adopts this tag** — not necessarily the layout on its
      `main` today. Both are read at the consumer's pinned tag, so a stale value
      reds the consumer's gate with no consumer-side fix.
      `tests/test_check_vendored_copies.py::TestShippedRegistry` fails on a
      stale sha; the paths are the manual half.
- [ ] **Cross-repo sequencing.** When a consumer's registry rows encode a layout
      that only exists on an unmerged branch of that consumer, the consumer's MR
      merges FIRST — a repo restructure (a move into `template/`, a renamed
      script directory) is the case that triggers it. Cut the tag first and
      every row for that consumer reds with no consumer-side fix: the registry
      is library-owned and the tag immutable, so only a follow-up patch release
      clears it. Verify against the MERGED tree, not a working copy:

      ```bash
      scripts/check-vendored-copies.py --consumer weisssrv-app-template \
        --repo-root <app-template checkout on main> --lib-path .
      scripts/check-vendored-copies.py --consumer weisssrv-cluster-template \
        --repo-root <cluster-template checkout on main> --lib-path .
      ```

      Nothing mechanical backstops this: the library's own pipeline no longer
      clones the templates, so the coupling is a manual step by design.
- [ ] The collection's
      [MIGRATING.md](../ansible_collections/weisssrv/infra/MIGRATING.md)
      `# Unreleased (next release)` heading is retitled to the tag being cut,
      and a fresh empty `# Unreleased (next release)` opened above it. It is the
      only per-release migration record for the collection (see
      [No changelog file](#no-changelog-file)); leaving it untitled makes the
      next cycle's delta read as one pending set with this one's. A release with
      nothing to migrate still gets a section saying so.
      `tests/test_docs_registry.py::TestMigratingSections` holds the newest
      titled section equal to `galaxy.yml`'s version, so this one is mechanical.
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
The module's own `required_version` is the same kind of promise and is checked
first: **raising the floor is BREAKING**, because a consumer below it cannot
`init` the module at all, however compatible the configuration is. It moves for
the module's shipped `tests/*.tftest.hcl` as well as for its configuration —
those run under the same constraint, and their mocking features have their own
floors (`mock_provider` 1.7, `override_during` 1.11).

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
