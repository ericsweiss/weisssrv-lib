"""Fixture consumer config whose multi-file pin has diverged."""

CONFIG = {
    "vars_file": "vars.yml",
    "cache_dir": ".version-cache",
    "default_deploy_command": "task infra:deploy",
    "services": [
        {
            "name": "Python CronJob Base",
            "var_name": "python_cronjob_version",
            "category": "dockerhub",
            "docker_image": "python",
            "version_file": [
                "kubernetes/apps/one/cronjob.yaml",
                "kubernetes/apps/three/cronjob.yaml",
            ],
        },
    ],
}
