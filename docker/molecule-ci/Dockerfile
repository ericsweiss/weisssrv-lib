# The JOB image molecule CI jobs run the script in (not the MOLECULE image the
# scenarios spin up): docker-cli, molecule, ansible and the pinned pip deps are
# baked in, so a job's before_script is a docker login rather than ~2 min of
# apt + pip + galaxy.
#
# The build context is THIS directory — requirements.txt + requirements.yml sit
# next to the Dockerfile, so the image is self-contained (docker/README.md).
#
#   docker build -t <registry>/molecule-ci:latest docker/molecule-ci/
# Pinned by manifest-list digest (multi-arch index — this image builds for
# amd64 + arm64) so the rolling `3.11` tag can't silently change the base.
# Bump deliberately: resolve the new digest with
#   docker buildx imagetools inspect python:3.11
FROM python:3.11@sha256:9139edd4aa848a1aa1c63865cd5d11b3a6aaaf3fb410a3ac7b3914e44092fe4c

# docker CLI for talking to the DinD sidecar. Install the static binary
# directly — the Debian docker.io package on Trixie doesn't include the
# CLI (only the daemon), and docker-ce-cli isn't in the default repos.
# Detect arch for multi-platform builds; verify the pinned sha256 before extract.
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

# pip dependencies (molecule + ansible + plugins)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --quiet --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Ansible collections
COPY requirements.yml /tmp/requirements.yml
RUN ansible-galaxy collection install -r /tmp/requirements.yml \
    && rm /tmp/requirements.yml
