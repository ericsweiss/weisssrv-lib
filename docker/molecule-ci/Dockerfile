# The job image molecule CI jobs run their script in, not the image the
# scenarios spin up: docker CLI, molecule, ansible and the pinned pip deps.
# Build context is THIS directory. Digest bump: docker buildx imagetools inspect python:3.14
FROM python:3.14@sha256:b9767f56a1cc620aa72d38cc3557d26412e38a4597c92953476136b78894ccbd

# Static docker CLI for the DinD sidecar: Debian's docker.io ships the daemon
# only, and docker-ce-cli is not in the default repos. Version and both shas are
# held equal to ci/build/docker-build.yml's defaults by tests/test_pin_parity.py.
RUN ARCH=$(dpkg --print-architecture) \
    && case "$ARCH" in \
         amd64) DOCKER_ARCH=x86_64; DOCKER_SHA256=9ac225999fd5e863f7694c728ef23d480616b1e5639328fa309befd4243d69eb;; \
         arm64) DOCKER_ARCH=aarch64; DOCKER_SHA256=873b3e0207857c3b2f28392fb922f3ea6efe37620c16d4d4aaf2fef409c90b73;; \
         *) echo "unsupported: $ARCH"; exit 1;; \
       esac \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${DOCKER_ARCH}/docker-29.9.2.tgz" -o /tmp/docker.tgz \
    && echo "${DOCKER_SHA256}  /tmp/docker.tgz" | sha256sum -c - \
    && tar xz --strip-components=1 -C /usr/local/bin -f /tmp/docker.tgz docker/docker \
    && rm /tmp/docker.tgz \
    && docker --version

COPY requirements.txt /tmp/requirements.txt
RUN pip install --quiet --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY requirements.yml /tmp/requirements.yml
RUN ansible-galaxy collection install -r /tmp/requirements.yml \
    && rm /tmp/requirements.yml
