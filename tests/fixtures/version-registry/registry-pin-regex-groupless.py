"""Fixture consumer config whose pin_regex declares no capture group."""

CONFIG = {
    "vars_file": "vars.yml",
    "cache_dir": ".version-cache",
    "default_deploy_command": "task infra:deploy",
    "version_file_aliases": {"ci": ".gitlab-ci.yml"},
    "services": [
        {
            "name": "Kustomize",
            "var_name": "kustomize_version",
            "category": "github",
            "github_repo": "kubernetes-sigs/kustomize",
            "version_file": "ci",
            "pin_regex": r'^\s*KUSTOMIZE_VERSION:\s*"[\w.+-]+"\s*$',
        },
    ],
}
