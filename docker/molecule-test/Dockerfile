# The systemd-enabled Debian container molecule spins up, with the roles' apt
# dependencies baked in; scenarios still pass without it. Build context is THIS
# directory. Digest bump: docker buildx imagetools inspect ghcr.io/hifis-net/debian-systemd:trixie
FROM ghcr.io/hifis-net/debian-systemd:trixie@sha256:d1ef7a336af9e98fb94bc0fe18d0bc7f863bae943c8cecadad2d8d08db09e687

ENV DEBIAN_FRONTEND=noninteractive

# Packages the collection's roles install via apt, consolidated into a single
# cached layer. Grouped by which role(s) need them.
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    # --- base role (common_packages) ---
    curl \
    wget \
    neovim \
    htop \
    tmux \
    screen \
    git \
    jq \
    unzip \
    rsync \
    net-tools \
    dnsutils \
    ca-certificates \
    gnupg \
    lsb-release \
    sudo \
    pciutils \
    # --- base role (SSH + misc); qemu-guest-agent is deliberately absent, the
    # base role's molecule verify asserts it is not installed in a container ---
    openssh-server \
    fail2ban \
    logrotate \
    cron \
    # --- k3s role ---
    open-iscsi \
    nfs-common \
    # --- nas_storage role ---
    nfs-kernel-server \
    samba \
    smartmontools \
    mergerfs \
    # --- smtp_relay / postfix_null_client ---
    postfix \
    libsasl2-modules \
    sasl2-bin \
    mailutils \
    # --- adguard_home ---
    python3-passlib \
    # --- unbound ---
    unbound \
    dns-root-data \
    # --- qol role ---
    zsh \
    fzf \
    ripgrep \
    bat \
    fd-find \
    # --- acme_certs role ---
    socat \
    # --- plex role ---
    apt-transport-https \
    # --- general test utilities ---
    iproute2 \
    iputils-ping \
    procps \
    ethtool \
    python3 \
    python3-apt
    # NOTE: intentionally NOT removing /var/lib/apt/lists/* — roles with
    # `update_cache: true` then hit a warm cache instead of re-downloading all
    # package metadata (~30MB image cost).

# AdGuard Home release tarball and its checksums.txt, staged where the
# adguard_home role looks so DNS scenarios install from disk. The version is
# held equal to what the scenarios pin by tests/test_pin_parity.py.
ARG ADGUARD_HOME_VERSION=0.107.78
# --retry: the one github.com fetch in the build; a single 5xx would fail it.
RUN ARCH=$(dpkg --print-architecture) \
    && AGH_URL="https://github.com/AdguardTeam/AdGuardHome/releases/download/v${ADGUARD_HOME_VERSION}" \
    && mkdir -p /var/cache/adguard-home /tmp/agh \
    && cd /tmp/agh \
    && curl -fsSL --retry 3 --retry-delay 5 --retry-connrefused \
       -O "${AGH_URL}/AdGuardHome_linux_${ARCH}.tar.gz" \
    && curl -fsSL --retry 3 --retry-delay 5 --retry-connrefused \
       -O "${AGH_URL}/checksums.txt" \
    && grep -E "[[:space:]][*]?([.]/)?AdGuardHome_linux_${ARCH}[.]tar[.]gz$" checksums.txt | sha256sum -c - \
    && mv "AdGuardHome_linux_${ARCH}.tar.gz" \
       "/var/cache/adguard-home/AdGuardHome_linux_${ARCH}-v${ADGUARD_HOME_VERSION}.tar.gz" \
    && cp checksums.txt /var/cache/adguard-home/checksums.txt \
    && cd / && rm -rf /tmp/agh

# Pre-create the ansible tmp directory (avoids "Failed to create
# temporary directory" errors during molecule prepare)
RUN mkdir -p /root/.ansible/tmp && chmod 700 /root/.ansible/tmp

# Roles must be what enables these: a package that auto-starts its service makes
# the idempotence check fail when systemd restarts it between plays. A unit
# missing here has been renamed, and fails the build instead of shipping enabled.
RUN for unit in apt-daily.timer apt-daily-upgrade.timer postfix unbound \
        nfs-kernel-server smbd nmbd ssh fail2ban; do \
      systemctl disable "$unit" || exit 1; \
    done
