"""Tests for scripts/check-ephemeral-storage-cap.py."""
from __future__ import annotations

import io

from script_loader import load_script

mod = load_script("check-ephemeral-storage-cap.py")


def _run(stdin_text: str, monkeypatch) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    try:
        return mod.main()
    except SystemExit as exc:  # the shared corpus loader exits 2 directly
        return int(exc.code)


def deployment(
    *,
    size_limit: str | None = "64Mi",
    requests: str | None = "64Mi",
    limits: str | None = "512Mi",
    mounted: bool = True,
) -> str:
    resources = []
    if requests is not None:
        resources.append("            requests:\n              ephemeral-storage: %s" % requests)
    if limits is not None:
        resources.append("            limits:\n              ephemeral-storage: %s" % limits)
    resource_block = (
        "          resources:\n" + "\n".join(resources) + "\n" if resources else ""
    )
    mount_block = (
        "          volumeMounts:\n            - name: tmp\n              mountPath: /tmp\n"
        if mounted else ""
    )
    empty_dir = (
        "            sizeLimit: %s\n" % size_limit if size_limit is not None else "            {}\n"
    )
    return (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata: {name: demo, namespace: apps}\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "        - name: app\n"
        + resource_block
        + mount_block
        + "      volumes:\n        - name: tmp\n          emptyDir:\n"
        + empty_dir
    )


def test_a_limit_above_the_sizelimit_passes(monkeypatch, capsys):
    assert _run(deployment(), monkeypatch) == 0
    assert "policy OK" in capsys.readouterr().out


def test_a_limit_equal_to_the_sizelimit_passes(monkeypatch):
    """Setting them equal is a deliberate shape, so `>` would be the wrong test."""
    assert _run(deployment(size_limit="64Mi", limits="64Mi"), monkeypatch) == 0


def test_a_limit_below_the_sizelimit_fails(monkeypatch, capsys):
    assert _run(deployment(size_limit="512Mi", limits="64Mi"), monkeypatch) == 1
    assert "evicts the pod before the volume fills" in capsys.readouterr().err


def test_mixed_units_are_compared_numerically(monkeypatch):
    assert _run(deployment(size_limit="1Gi", limits="512Mi"), monkeypatch) == 1
    assert _run(deployment(size_limit="512Mi", limits="1Gi"), monkeypatch) == 0


def test_a_missing_limit_fails(monkeypatch, capsys):
    assert _run(deployment(limits=None), monkeypatch) == 1
    assert "resources.limits" in capsys.readouterr().err


def test_a_missing_request_fails(monkeypatch, capsys):
    """The request is what the scheduler places the pod on."""
    assert _run(deployment(requests=None), monkeypatch) == 1
    assert "resources.requests" in capsys.readouterr().err


def test_an_emptydir_without_a_sizelimit_is_not_a_subject(monkeypatch):
    assert _run(deployment(size_limit=None, requests=None, limits=None), monkeypatch) == 0


def test_a_container_that_does_not_mount_it_is_not_a_subject(monkeypatch):
    assert _run(
        deployment(mounted=False, requests=None, limits=None), monkeypatch
    ) == 0


SEVERAL = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: demo, namespace: apps}
spec:
  template:
    spec:
      containers:
        - name: app
          resources:
            requests: {ephemeral-storage: 64Mi}
            limits: {ephemeral-storage: 100Mi}
          volumeMounts:
            - {name: tmp, mountPath: /tmp}
            - {name: cache, mountPath: /cache}
      volumes:
        - name: tmp
          emptyDir: {sizeLimit: 64Mi}
        - name: cache
          emptyDir: {sizeLimit: 64Mi}
"""


def test_several_sized_emptydirs_share_one_budget(monkeypatch, capsys):
    """Both draw on the same limit, so the pod is evicted before either fills."""
    assert _run(SEVERAL, monkeypatch) == 1
    assert "tmp=64Mi, cache=64Mi" in capsys.readouterr().err


CRONJOB = """
apiVersion: batch/v1
kind: CronJob
metadata: {name: prune, namespace: apps}
spec:
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: prune
              resources:
                requests: {ephemeral-storage: 8Mi}
                limits: {ephemeral-storage: 8Mi}
              volumeMounts:
                - {name: tmp, mountPath: /tmp}
          volumes:
            - name: tmp
              emptyDir: {sizeLimit: 1Gi}
"""


def test_a_cronjob_pod_template_is_reached(monkeypatch):
    """The template nests two levels deeper, so a shallow walk would miss it."""
    assert _run(CRONJOB, monkeypatch) == 1


INIT_CONTAINER = """
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: demo, namespace: apps}
spec:
  template:
    spec:
      initContainers:
        - name: seed
          volumeMounts:
            - {name: tmp, mountPath: /tmp}
      containers:
        - name: app
          resources:
            requests: {ephemeral-storage: 64Mi}
            limits: {ephemeral-storage: 64Mi}
          volumeMounts:
            - {name: tmp, mountPath: /tmp}
      volumes:
        - name: tmp
          emptyDir: {sizeLimit: 64Mi}
"""


def test_an_init_container_is_held_to_the_same_rule(monkeypatch, capsys):
    assert _run(INIT_CONTAINER, monkeypatch) == 1
    assert "'seed'" in capsys.readouterr().err


def test_an_unreadable_sizelimit_is_reported(monkeypatch, capsys):
    assert _run(deployment(size_limit="64 Mi"), monkeypatch) == 1
    assert "not a readable quantity" in capsys.readouterr().err


def test_a_corpus_with_no_pod_spec_is_an_operator_error(monkeypatch, capsys):
    assert _run("kind: ConfigMap\nmetadata: {name: x}\n", monkeypatch) == 2
    assert "inspected 0 pod specs" in capsys.readouterr().err


def test_an_empty_corpus_is_an_operator_error(monkeypatch):
    assert _run("", monkeypatch) == 2


def test_quantity_parsing_covers_the_suffixes_it_claims():
    assert mod.parse_quantity("1Gi") == 2**30
    assert mod.parse_quantity("1G") == 10**9
    assert mod.parse_quantity("1024") == 1024
    assert mod.parse_quantity("1e3") == 1000
    assert mod.parse_quantity("1Qi") is None
    assert mod.parse_quantity(None) is None
