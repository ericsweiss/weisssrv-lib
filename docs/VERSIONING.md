# Versioning & tag pinning

## Consume by tag, never by branch

Every consumer pins a **release tag** in its `include: ref:`:

```yaml
include:
  - project: eric/weisssrv-lib
    ref: v0.1.0
    file: /ci/lint/yaml-lint.yml
```

A floating `main` (or `~latest`) ref would auto-propagate a library change into
both weisssrv's and every tenant's pipeline **with no review** — a supply-chain
and reproducibility hazard. Pinning a tag makes every pipeline reproducible and
turns a library upgrade into an explicit, reviewable bump.

## Release tags

- Tags are `vMAJOR.MINOR.PATCH` (semver), starting at **v0.1.0**.
- **MAJOR** — a breaking change to a template's inputs or behavior (a renamed or
  removed input, a changed default that alters a consumer's resolved pipeline).
- **MINOR** — a new template, a new input with a back-compatible default, or a
  new script/CLI capability.
- **PATCH** — a fix that does not change any documented input or default.

Cut a tag from `main` after the change merges. The tag is immutable — never move
a published tag (a consumer that pinned it would silently get different CI).

## Upgrading a consumer

There is **no hosted Renovate** on this instance, so bumps are manual (mirroring
weisssrv's `task maintenance:check-versions` discipline):

1. Read the target tag's GitLab release notes (or its annotated-tag message) for
   what changed — this repo tracks per-tag changes there, not in a CHANGELOG file.
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

## CI Catalog (future)

The instance (GitLab 19.2) supports CI Catalog components, which would add a
versioned UI + `include: component:` semver resolution. This library starts with
plain tag-pinned `include: project:` for simplicity; promoting it to a catalog
resource is a later, additive step and does not change the pinning discipline
above.
