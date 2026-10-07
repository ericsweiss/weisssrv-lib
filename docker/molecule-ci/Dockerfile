# The job image molecule CI jobs run their script in, not the image the
# scenarios spin up: docker CLI, molecule, ansible and the pinned pip deps.
# Build context is THIS directory. Digest bump: docker buildx imagetools inspect python:3.11
FROM python:3.11@sha256:9139edd4aa848a1aa1c63865cd5d11b3a6aaaf3fb410a3ac7b3914e44092fe4c

# Static docker CLI for the DinD sidecar: Debian's docker.io ships the daemon
# only, and docker-ce-cli is not in the default repos. Version and both shas are
# held equal to ci/build/docker-build.yml's defaults by tests/test_pin_parity.py.
RUN ARCH=$(dpkg --print-architecture) \
    && case "$ARCH" in \
         amd64) DOCKER_ARCH=x86_64; DOCKER_SHA256=4f798b3ee1e0140eab5bf30b0edc4e84f4cdb53255a429dc3bbae9524845d640;; \
         arm64) DOCKER_ARCH=aarch64; DOCKER_SHA256=e6b53725a73763ab3f988c73f8772eaed429754c1a579db5ff11f21990fd1817;; \
         *) echo "unsupported: $ARCH"; exit 1;; \
       esac \
    && curl -fsSL "https://download.docker.com/linux/static/stable/${DOCKER_ARCH}/docker-27.5.1.tgz" -o /tmp/docker.tgz \
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
