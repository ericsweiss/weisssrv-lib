# docker/

Build contexts for the images this library publishes: the two the molecule CI
toolkit consumes, and the one a consumer's Ansible deploy jobs run in.

| Image | Context | What it is |
|---|---|---|
| `molecule-ci` | `docker/molecule-ci/` | the **job** image CI runs the script in: docker CLI (for the DinD sidecar) + the pinned molecule/ansible pip deps + galaxy collections |
| `molecule-test` | `docker/molecule-test/` | the **test** image molecule spins up: systemd-enabled Debian with the roles' apt dependencies pre-installed |
| `ansible-deploy` | `docker/ansible-deploy/` | the **job** image a deploy runs in: op CLI + pinned ansible + git/ssh/jq, so a deploy job stops rebuilding its toolchain every run — [details](ansible-deploy/README.md) |

Every base is pinned by manifest-list digest; bump deliberately with
`docker buildx imagetools inspect <image>:<tag>`.

`molecule-test` also stages the AdGuard Home release tarball under
`/var/cache/adguard-home` (the `adguard_home` role's
`adguard_home_archive_cache_dir`) so DNS scenarios install from disk instead of
fetching github.com mid-test. Its `ADGUARD_HOME_VERSION` build arg must track
the version the scenarios pin; a mismatch costs the cache, not the run.

## Consuming the published images

This library's own pipeline builds and pushes all three images to its project
registry, so a consumer does not need these build contexts at all:

| Tag | Written by | Use it for |
|---|---|---|
| `:<short-sha>` | every build (MR + default branch) | the exact image one pipeline built |
| `:latest` | default-branch builds only | "current", mutable |
| `:vX.Y.Z` | `publish-image-tags`, on every release | **pinning** — same tag a consumer already pins its `include: ref:` at |

```yaml
variables:
  MOLECULE_CI_IMAGE: "$CI_REGISTRY/eric/weisssrv-lib/molecule-ci:$WEISSSRV_LIB_REF"
  MOLECULE_TEST_IMAGE: "$CI_REGISTRY/eric/weisssrv-lib/molecule-test:$WEISSSRV_LIB_REF"
  ANSIBLE_DEPLOY_IMAGE: "$CI_REGISTRY/eric/weisssrv-lib/ansible-deploy:$WEISSSRV_LIB_REF"
```

Cross-project pulls need weisssrv-lib to allow the consumer project on its
CI/CD job-token allowlist (Settings > CI/CD > Job token permissions).

## Building them yourself

A consumer that needs different pins builds from its own context with this
library's `ci/build/docker-build.yml` template — the extracted `build_and_push`
helper (sha-pinned static docker CLI, digest-pinned DinD, dind wait, registry
layer cache + inline cache, bounded retry, `:<sha>` always + `:latest` on the
default branch). The `ref` below is an example: use the tag your repo pins
(docs/VERSIONING.md).

```yaml
include:
  - project: eric/weisssrv-lib
    ref: v0.11.0
    file: /ci/build/docker-build.yml
    inputs:
      job_name: build-molecule-ci
      image_name: molecule-ci
      context: docker/molecule-ci      # self-contained: requirements live here
      tags: [<privileged-runner-tag>]
```

`docker-build` needs a **privileged** runner (DinD). The shared tag-less runner
cannot build.

Build each image as a **separate job** scoped to its own path
(`docker/molecule-ci/**` vs `docker/molecule-test/**` vs
`docker/ansible-deploy/**`): a pip pin bump must not rebuild molecule-test's
~40-package apt layer, an apt change must not rebuild molecule-ci's pip + galaxy
layers, and neither must rebuild the deploy image. That is how this library's
own pipeline wires them — three `docker-build` includes with disjoint `changes`.

## Pins are per-image, not per-repo

`molecule-ci` COPYs `requirements.txt` and `requirements.yml` — and
`ansible-deploy` its `requirements.txt` — **from its own directory**, so the
build context is that directory and each image is self-contained. A consumer
that pins different versions either edits its copy of these files or builds the
Dockerfile against its own context. That is a deliberate trade: one duplicated
pin set in exchange for an image that builds from this repo without assuming the
consumer's layout.
