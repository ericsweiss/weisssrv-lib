"""Fixture consumer config for pins that are not `image:` lines.

A CI variable and a `services: - name:` entry, each matched by its own
pin_regex, plus one entry whose regex captures nothing.
"""

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
            "pin_regex": r'^\s*KUSTOMIZE_VERSION:\s*"([\w.+-]+)"\s*$',
        },
        {
            "name": "Docker dind",
            "var_name": "docker_dind_version",
            "category": "dockerhub",
            "docker_image": "library/docker",
            "version_file": "ci",
            "pin_regex": r"^\s*- name:\s*docker:([\w.+-]+?)(?:@sha256:[0-9a-f]+)?\s*$",
        },
    ],
}
