# The job image an Ansible deploy runs in: 1Password CLI, ansible, and the
# git/ssh/jq toolchain. Build context is THIS directory; contents: ./README.md.
# Digest bump: docker buildx imagetools inspect python:3.11-slim
FROM python:3.11-slim@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff

ENV DEBIAN_FRONTEND=noninteractive

# The toolchain a deploy job shells out to: git for ansible-galaxy's git+ URLs,
# openssh-client for Ansible's transport and the TOFU ssh-keyscan, jq for the
# kubectl JSON parsing, curl + ca-certificates for downloads, unzip for `op`.
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        ca-certificates \
        curl \
        git \
        jq \
        openssh-client \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# 1Password CLI from the versioned archive, not the apt repo: that pool keeps
# only the current version, so an apt install cannot be pinned reproducibly.
# Bump the version and both shas together; ./README.md carries the sha recipe.
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

COPY requirements.txt /tmp/requirements.txt
RUN pip install --quiet --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && ansible --version
