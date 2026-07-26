"""Consumer config for scripts/check-versions.py.

Copy to `scripts/version-registry.py` (the default lookup path) and edit. A
`.json` file with the same keys works too; the Python form exists so each entry
can carry the inline rationale a JSON registry would lose — the "why is this
pinned / held / not auto-bumped" notes are the most valuable part of a registry.

Entry fields:
  name            display name (unique)
  var_name        the pin's key in vars_file; `helm_chart_versions.<chart>` for
                  a nested helm pin; any key for a version_file pin
  category        github | dockerhub | ghcr | lsio | helm | apt_repo | manual
  deploy_command  how to roll the bump out; falls back to default_deploy_command
  version_file    the pin lives OUTSIDE vars_file: a version_file_aliases key,
                  a repo-relative path, or a list of paths that must agree
  held            reported but never written (a documented upstream block);
                  say why in `notes`
  tag_filter      (github) regex the upstream tag must match
  tag_regex       (dockerhub/ghcr/lsio) regex the image tag must match
  strip_prefix    (github) drop version_prefix from the recorded version
"""

CONFIG = {
    # Where the pins live. Every path here is repo-root relative.
    "vars_file": "ansible/inventories/prod/group_vars/all.yml",
    "cache_dir": ".version-cache",
    # Used when an entry has no deploy_command and is not a version_file pin.
    "default_deploy_command": "task infra:deploy",
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
            "apt_url": "https://pkgs.tailscale.com/stable/debian/dists/trixie/main/binary-amd64/Packages.gz",
            "apt_package": "tailscale",
            "deploy_command": "task maintenance:update-applications",
        },
        {
            # A pin that must be bumped WITH its @sha256 digest, so it is never
            # auto-written — check-versions only reports staleness.
            "name": "PR Agent",
            "var_name": "pr_agent_version",
            "category": "dockerhub",
            "docker_image": "codiumai/pr-agent",
            "version_file": "ci",
        },
        {
            # Held: an update exists but is deliberately not taken. Reported
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
