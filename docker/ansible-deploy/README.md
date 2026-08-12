# ansible-deploy image

The job image an Ansible deploy runs in. Every deploy job built its toolchain
from scratch on each run — the op CLI's apt repo, `pip install ansible`, the git
install — which measured at over half the wall time of a 115s weisssrv deploy
job. This image bakes that half.

## What it bakes

| Baked | Pin |
| --- | --- |
| base | `python:3.11-slim`, manifest-list digest |
| 1Password CLI (`op`) | version + per-arch sha256 in the Dockerfile |
| `ansible` | `requirements.txt` (exact `==` pin) |
| `git`, `openssh-client`, `jq`, `curl`, `ca-certificates`, `unzip` | apt, unpinned (Debian stable) |
| python3 + pip | from the base |

## What stays per-run

- **`ansible-galaxy install -r <ansible_dir>/requirements.yml`** — the
  collection pin is site data and changes per consumer and per bump, so baking
  it would ship a stale platform. This is the one install a deploy job keeps.
- **Every secret**: the SSH key, the kubeconfig, and each job's `op://`
  variable map are read at run time by `op`.
- **The TOFU `ssh-keyscan`** — ephemeral runners inherit no `known_hosts`, and
  baking host keys would freeze them at image-build time.
- **`kubectl`** — [`ci/deploy/kubectl-setup.yml`](../../ci/deploy/kubectl-setup.yml)
  installs its own sha256-pinned copy over whatever is on PATH, so a copy here
  would be a second pin for the same tool that no job would ever use.

## Adopting it

Point a deploy job's `image:` at the published tag (see
[../README.md](../README.md) for the tag scheme and the cross-project pull
prerequisite):

```yaml
deploy-ansible-base:
  image: "$CI_REGISTRY/eric/weisssrv-lib/ansible-deploy:$WEISSSRV_LIB_REF"
```

The library's `ci/deploy/` templates still run their installs unconditionally,
so on this image they are re-installs of what is already there — cheap, but not
free. Taking the full win means the consuming job also drops the corresponding
`before_script` steps (the `!reference [.install-1password, before_script]`, the
`apt_packages` install, the `pip install ansible`); that is a consumer-side
change, and the templates are unchanged by this image.

## Bumping a pin

Each pin is bumped by hand — nothing tracks upstream for this image.

- **Base image**: `docker buildx imagetools inspect python:3.11-slim`, and
  replace the digest on the `FROM` line. Use the INDEX digest, not a
  platform-specific one, or the image stops building on the other arch.
- **`op`**: pick the version, then take both shas —
  `curl -fsSL https://cache.agilebits.com/dist/1P/op2/pkg/v<version>/op_linux_<arch>_v<version>.zip | sha256sum`
  for `amd64` and `arm64` — and move the version and both literals together.
  The archive keeps old versions; 1Password's apt pool does not, which is why
  the image does not use the apt path
  [`ci/templates/install-1password.yml`](../../ci/templates/install-1password.yml)
  takes.
- **`ansible`**: edit `requirements.txt`. It is held equal to
  `ci/deploy/deploy-base.yml`'s `ansible_version` default by
  `tests/test_ansible_deploy_image.py`, so both move in the same MR.

A push to `main` under `docker/ansible-deploy/**` rebuilds and publishes;
`docs/VERSIONING.md` covers the `:vX.Y.Z` retag that a consumer pins.
