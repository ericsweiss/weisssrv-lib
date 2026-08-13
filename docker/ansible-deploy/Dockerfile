# The JOB image an Ansible deploy runs in: the 1Password CLI, ansible at the
# pinned version, and the git/ssh/jq toolchain the deploy jobs shell out to are
# baked in, so a job's before_script stops re-installing them every run (over
# half of a measured 115s deploy job in weisssrv).
#
# NOT baked, deliberately:
#   - the weisssrv.infra collection. Its pin is per-consumer
#     (ansible/requirements.yml), so `ansible-galaxy install -r` stays the job's
#     one remaining install.
#   - kubectl. ci/deploy/kubectl-setup.yml installs its own sha256-pinned copy
#     over whatever is on PATH, and the kubeconfig it reads from 1Password can
#     never be baked — so a copy here would be a second pin for the same tool
#     that no job would use.
#
# The build context is THIS directory — requirements.txt sits next to the
# Dockerfile, so the image is self-contained (docker/README.md).
#
#   docker build -t <registry>/ansible-deploy:latest docker/ansible-deploy/
# Pinned by manifest-list digest (the multi-arch index, not a platform-specific
# digest) so the rolling `3.11-slim` tag can't silently change the base and the
# Dockerfile stays buildable on either arch. The PUBLISHED tag is amd64-only:
# ci/build/docker-build.yml runs a plain `docker build` on the amd64
# `infrastructure` runner, so the arm64 arm below is never exercised in CI. It
# is kept for a future multi-arch (`buildx --platform`) build; until then its
# sha256 is unverified by any job.
# Same 3.11 line the deploy jobs run on today, slim because everything the
# toolchain needs is installed explicitly below.
# Bump deliberately: resolve the new digest with
#   docker buildx imagetools inspect python:3.11-slim
FROM python:3.11-slim@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff

# Suppress interactive prompts during apt installs
ENV DEBIAN_FRONTEND=noninteractive

# The toolchain a deploy job uses directly: git (ansible-galaxy installs the
# collection from a git+ URL), openssh-client (Ansible's transport and the TOFU
# ssh-keyscan), jq (kubectl JSON parsing in the verify/maintenance jobs), curl +
# ca-certificates (every download, including kubectl-setup's), unzip (the op
# archive below).
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        ca-certificates \
        curl \
        git \
        jq \
        openssh-client \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# 1Password CLI, from the versioned archive rather than the apt repo
# ci/templates/install-1password.yml uses: that repo carries only the CURRENT
# version (older .debs are pruned from the pool), so an apt install cannot be
# pinned to a version a rebuild will still find. Arch is detected so the
# Dockerfile stays portable, but only the amd64 arm runs in CI (see the FROM
# comment); the sha256 is verified before extract either way, the same
# discipline every other downloaded binary in this repo gets.
# Bump: change all three literals together, taking the shas from
#   curl -fsSL .../op_linux_<arch>_v<version>.zip | sha256sum
RUN ARCH=$(dpkg --print-architecture) \
    && case "$ARCH" in \
         amd64) OP_SHA256=7a7b7168f521d31838f0cc59abf172527853308c9b9cceee34895121acb0be0f;; \
         arm64) OP_SHA256=854f92932dcfebc26434e83e426729933a8d0aae447889e439cf01f1b213a7fd;; \
         *) echo "unsupported: $ARCH"; exit 1;; \
       esac \
    && curl -fsSL "https://cache.agilebits.com/dist/1P/op2/pkg/v2.38.1/op_linux_${ARCH}_v2.38.1.zip" -o /tmp/op.zip \
    && echo "${OP_SHA256}  /tmp/op.zip" | sha256sum -c - \
    && unzip -j -o /tmp/op.zip op -d /usr/local/bin \
    && chmod 0755 /usr/local/bin/op \
    && rm /tmp/op.zip \
    && op --version

# pip dependencies (ansible, at the version a deploy job would otherwise install)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --quiet --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && ansible --version
