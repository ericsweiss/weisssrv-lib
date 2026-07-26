# docker/

Build contexts for the two images the molecule CI toolkit consumes.

| Image | Context | What it is |
|---|---|---|
| `molecule-ci` | `docker/molecule-ci/` | the **job** image CI runs the script in: docker CLI (for the DinD sidecar) + the pinned molecule/ansible pip deps + galaxy collections |
| `molecule-test` | `docker/molecule-test/` | the **test** image molecule spins up: systemd-enabled Debian with the roles' apt dependencies pre-installed |

Both bases are pinned by manifest-list digest; bump deliberately with
`docker buildx imagetools inspect <image>:<tag>`.

## Building them

Use this library's own `ci/build/docker-build.yml` template — it is the extracted
`build_and_push` helper (sha-pinned static docker CLI, dind wait, registry layer
cache + inline cache, bounded retry, `:<sha>` always + `:latest` on the default
branch):

```yaml
include:
  - project: eric/weisssrv-lib
    ref: v0.2.0
    file: /ci/build/docker-build.yml
    inputs:
      job_name: build-molecule-ci
      image_name: molecule-ci
      context: docker/molecule-ci      # self-contained: requirements live here
      tags: [<privileged-runner-tag>]
```

`docker-build` needs a **privileged** runner (DinD). The shared tag-less runner
cannot build.

Build the two images as **separate jobs** scoped to their own paths
(`docker/molecule-ci/**` vs `docker/molecule-test/**`): a pip pin bump must not
rebuild molecule-test's ~40-package apt layer, and an apt change must not
rebuild molecule-ci's pip + galaxy layers.

## Pins are per-image, not per-repo

`molecule-ci` COPYs `requirements.txt` and `requirements.yml` **from its own
directory**, so the build context is `docker/molecule-ci/` and the image is
self-contained. A consumer that pins different versions either edits its copy of
these files or builds the Dockerfile against its own context. That is a
deliberate trade: one duplicated pin set in exchange for an image that builds
from this repo without assuming the consumer's layout.
