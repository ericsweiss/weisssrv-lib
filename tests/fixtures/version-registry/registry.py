"""Fixture consumer config for scripts/check-versions.py.

Small stand-in for a real registry: one entry per lookup category, one pin that
lives outside the vars file (`version_file`), one multi-file pin, one held entry,
and one allow-listed untracked pin. Paths resolve against tests/fixtures/
version-registry/repo (the tests pass it as repo_root).
"""

CONFIG = {
    "vars_file": "vars.yml",
    "cache_dir": ".version-cache",
    "default_deploy_command": "task infra:deploy",
    "version_file_aliases": {"ci": ".gitlab-ci.yml"},
    "untracked_allowlist": ["debian_version"],
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
            "name": "Registry Cache",
            "var_name": "registry_cache_version",
            "category": "dockerhub",
            "docker_image": "library/registry",
            "tag_regex": r"^\d+\.\d+\.\d+$",
            "deploy_command": "task flux:sync-versions && git push",
        },
        {
            "name": "Traefik Chart",
            "var_name": "helm_chart_versions.traefik",
            "category": "helm",
            "helm_repo": "https://traefik.github.io/charts",
            "helm_chart": "traefik",
            "deploy_command": "task flux:sync-versions && git push",
        },
        {
            # Held: reported but never written by --update / --update-all.
            "name": "MetalLB Chart",
            "var_name": "helm_chart_versions.metallb",
            "category": "helm",
            "helm_repo": "https://metallb.github.io/metallb",
            "helm_chart": "metallb",
            "held": True,
            "notes": "held on an open upstream regression",
            "deploy_command": "task flux:sync-versions && git push",
        },
        {
            # Digest-locked CI image pin: read from the `ci` alias, never written.
            "name": "PR Agent",
            "var_name": "pr_agent_version",
            "category": "dockerhub",
            "docker_image": "codiumai/pr-agent",
            "version_file": "ci",
        },
        {
            # One tag pinned in two manifests that must agree.
            "name": "Python CronJob Base",
            "var_name": "python_cronjob_version",
            "category": "dockerhub",
            "docker_image": "python",
            "version_file": [
                "kubernetes/apps/one/cronjob.yaml",
                "kubernetes/apps/two/cronjob.yaml",
            ],
        },
        {
            "name": "Manual Thing",
            "var_name": "manual_thing_version",
            "category": "manual",
            "notes": "no upstream feed; bumped by hand",
        },
    ],
}
