"""Consumer config for scripts/check-versions.py.

Copy to scripts/version-registry.py and edit; the .py form exists so each entry
can carry its own why-pinned note. Field reference: docs/SCRIPTS.md.
"""

CONFIG = {
    # Where the pins live. Every path here is repo-root relative.
    "vars_file": "ansible/inventories/prod/group_vars/all.yml",
    "cache_dir": ".version-cache",
    # Used when an entry has no deploy_command and is not a version_file pin.
    "default_deploy_command": "task infra:deploy",
    # Heading on the table report.
    "report_title": "Homelab Version Check Report",
    # Named files that hold digest-locked `image:` pins outside vars_file.
    "version_file_aliases": {"ci": ".gitlab-ci.yml"},
    # Pins with no upstream to track — `--check-coverage` ignores these.
    "untracked_allowlist": [
        "debian_version",  # set by the distro, not a per-service upstream
    ],
    "services": [
        {
            "name": "k3s",
            "var_name": "k3s_version",
            "category": "github",
            "github_repo": "k3s-io/k3s",
            "version_prefix": "v",
            "strip_prefix": False,
            "tag_filter": r"^v\d+\.\d+\.\d+\+k3s\d+$",
            "deploy_command": "task maintenance:update-k3s-nodes",
            # Couples the installer hash to the pinned version, checked by
            # scripts/check-version-checksums.py. coupled_vars makes --update-all
            # emit a PAIRED-EDIT-REQUIRED block instead of bumping alone.
            "checksum_var": "k3s_install_script_checksum",
            "checksum_url": "https://raw.githubusercontent.com/k3s-io/k3s/{version}/install.sh",
            "coupled_vars": ["k3s_install_script_checksum"],
            "notes": "on bump re-download get.k3s.io and recompute k3s_install_script_checksum",
        },
        {
            "name": "Traefik Chart",
            "var_name": "helm_chart_versions.traefik",
            "category": "helm",
            "helm_repo": "https://traefik.github.io/charts",
            "helm_chart": "traefik",
            "deploy_command": "task flux:sync-versions && git commit && git push",
        },
        {
            "name": "Registry Cache",
            "var_name": "registry_cache_version",
            "category": "dockerhub",
            "docker_image": "library/registry",
            # Bare X.Y.Z only — never a floating 3/3.1 or a 3.0.0-rc.N.
            "tag_regex": r"^\d+\.\d+\.\d+$",
            "deploy_command": "task flux:sync-versions && git commit && git push",
        },
        {
            "name": "Gluetun",
            "var_name": "gluetun_version",
            "category": "ghcr",
            "ghcr_image": "qdm12/gluetun",
            "tag_regex": r"^v\d+\.\d+\.\d+$",
            "deploy_command": "task flux:sync-versions && git commit && git push",
        },
        {
            "name": "Tailscale",
            "var_name": "tailscale_version",
            "category": "apt_repo",
            # A list is tried in order, so a suite rename does not break the
            # lookup before the hosts move.
            "apt_url": [
                "https://pkgs.tailscale.com/stable/debian/dists/trixie/main/binary-amd64/Packages.gz",
                "https://pkgs.tailscale.com/stable/debian/dists/bookworm/main/binary-amd64/Packages.gz",
            ],
            "apt_package": "tailscale",
            "deploy_command": "task maintenance:update-applications",
        },
        {
            # A pin that must be bumped WITH its @sha256 digest, so it is never
            # auto-written — check-versions only reports staleness.
            "name": "PR Agent",
            "var_name": "pr_agent_version",
            "category": "dockerhub",
            "docker_image": "pragent/pr-agent",
            "version_file": "ci",
        },
        # CI tooling: pinned in the pipeline rather than in vars_file, which is
        # what version_file_aliases maps.
        {
            "name": "kustomize",
            "var_name": "KUSTOMIZE_VERSION",
            "version_file": "ci",
            "category": "github",
            "github_repo": "kubernetes-sigs/kustomize",
            "tag_filter": r"^kustomize/v\d+\.\d+\.\d+$",
        },
        {
            "name": "kubeconform",
            "var_name": "KUBECONFORM_VERSION",
            "version_file": "ci",
            "category": "github",
            "github_repo": "yannh/kubeconform",
            "version_prefix": "v",
            "strip_prefix": True,
        },
        {
            "name": "helm",
            "var_name": "HELM_VERSION",
            "version_file": "ci",
            "category": "github",
            "github_repo": "helm/helm",
            # The 3.x line only, so the bot never proposes helm 4.
            "tag_filter": r"^v3\.\d+\.\d+$",
        },
        {
            # Held: an update exists but is not taken. Reported
            # without flipping the exit code or re-posting an MR comment.
            "name": "MetalLB Chart",
            "var_name": "helm_chart_versions.metallb",
            "category": "helm",
            "helm_repo": "https://metallb.github.io/metallb",
            "helm_chart": "metallb",
            "held": True,
            "notes": "held on an open upstream regression — unhold when it ships",
        },
    ],
}
