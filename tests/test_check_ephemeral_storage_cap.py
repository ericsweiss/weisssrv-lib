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


def test_a_memory_backed_emptydir_needs_no_pair(monkeypatch):
    """tmpfs is charged to the memory limit, not to ephemeral storage."""
    doc = deployment(size_limit="512Mi", limits=None).replace(
        "          emptyDir:\n", "          emptyDir:\n            medium: Memory\n"
    )
    assert _run(doc, monkeypatch) == 0


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


SHARED = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: demo, namespace: apps}
spec:
  template:
    spec:
      containers:
        - name: app
          resources:
            requests: {ephemeral-storage: 256Mi}
            limits: {ephemeral-storage: 256Mi}
          volumeMounts:
            - {name: tmp, mountPath: /tmp}
        - name: helper
          resources:
            requests: {ephemeral-storage: 256Mi}
            limits: {ephemeral-storage: 256Mi}
          volumeMounts:
            - {name: tmp, mountPath: /shared}
      volumes:
        - name: tmp
          emptyDir: {sizeLimit: 512Mi}
"""


def test_a_shared_emptydir_is_charged_once(monkeypatch):
    """One volume, two mounts: 512Mi against a 512Mi pod budget, not 1Gi."""
    assert _run(SHARED, monkeypatch) == 0


AGGREGATE = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: demo, namespace: apps}
spec:
  template:
    spec:
      containers:
        - name: app
          resources:
            requests: {ephemeral-storage: 32Mi}
            limits: {ephemeral-storage: 32Mi}
          volumeMounts:
            - {name: tmp, mountPath: /tmp}
        - name: helper
          resources:
            requests: {ephemeral-storage: 32Mi}
            limits: {ephemeral-storage: 32Mi}
          volumeMounts:
            - {name: cache, mountPath: /cache}
      volumes:
        - name: tmp
          emptyDir: {sizeLimit: 64Mi}
        - name: cache
          emptyDir: {sizeLimit: 64Mi}
"""


def test_unique_sizelimits_above_the_aggregate_limit_fail(monkeypatch, capsys):
    """The budget is the pod's, so it is one finding against the summed limits."""
    assert _run(AGGREGATE, monkeypatch) == 1
    err = capsys.readouterr().err
    assert "mounts 128Mi of sized emptyDir (tmp=64Mi, cache=64Mi)" in err
    assert "limit of 64Mi" in err
    assert err.count("apps/Deployment/demo") == 1


BYSTANDER = """
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
            limits: {ephemeral-storage: 64Mi}
          volumeMounts:
            - {name: tmp, mountPath: /tmp}
        - name: bystander
          resources:
            limits: {ephemeral-storage: 512Mi}
      volumes:
        - name: tmp
          emptyDir: {sizeLimit: 512Mi}
"""


def test_a_container_that_mounts_nothing_still_funds_the_budget(monkeypatch):
    """The kubelet weighs pod usage against every container's limit."""
    assert _run(BYSTANDER, monkeypatch) == 0


FLOOR = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: demo, namespace: apps}
spec:
  template:
    spec:
      initContainers:
        - name: seed
          resources:
            requests: {ephemeral-storage: 1Gi}
            limits: {ephemeral-storage: 1Gi}
          volumeMounts:
            - {name: tmp, mountPath: /tmp}
      containers:
        - name: app
          resources:
            requests: {ephemeral-storage: 8Mi}
            limits: {ephemeral-storage: 8Mi}
          volumeMounts:
            - {name: tmp, mountPath: /tmp}
      volumes:
        - name: tmp
          emptyDir: {sizeLimit: 512Mi}
"""


def test_a_plain_init_container_raises_the_floor(monkeypatch):
    """It runs alone, so the kubelet takes its limit as the pod's, not a term of it."""
    assert _run(FLOOR, monkeypatch) == 0


SIDECAR = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: demo, namespace: apps}
spec:
  template:
    spec:
      initContainers:
        - name: logger
          restartPolicy: Always
          resources:
            requests: {ephemeral-storage: 64Mi}
            limits: {ephemeral-storage: 64Mi}
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
          emptyDir: {sizeLimit: 128Mi}
"""


def test_a_sidecar_init_container_adds_to_the_budget(monkeypatch):
    """A restartable init container runs beside the app, so its limit is a term."""
    assert _run(SIDECAR, monkeypatch) == 0


def _init_phase(order: tuple[str, ...]) -> str:
    """A pod whose init phase holds a sidecar and a plain init, in this order."""
    blocks = {
        "sidecar": (
            "        - name: logger\n          restartPolicy: Always\n"
            "          resources:\n"
            "            requests: {ephemeral-storage: 64Mi}\n"
            "            limits: {ephemeral-storage: 64Mi}\n"
            "          volumeMounts:\n            - {name: tmp, mountPath: /tmp}\n"
        ),
        "init": (
            "        - name: seed\n          resources:\n"
            "            requests: {ephemeral-storage: 64Mi}\n"
            "            limits: {ephemeral-storage: 64Mi}\n"
            "          volumeMounts:\n            - {name: tmp, mountPath: /tmp}\n"
        ),
    }
    return (
        "apiVersion: apps/v1\nkind: Deployment\n"
        "metadata: {name: demo, namespace: apps}\n"
        "spec:\n  template:\n    spec:\n      initContainers:\n"
        + "".join(blocks[name] for name in order)
        + "      containers:\n        - name: app\n          resources:\n"
        "            requests: {ephemeral-storage: 8Mi}\n"
        "            limits: {ephemeral-storage: 8Mi}\n"
        "          volumeMounts:\n            - {name: tmp, mountPath: /tmp}\n"
        "      volumes:\n        - name: tmp\n"
        "          emptyDir: {sizeLimit: 128Mi}\n"
    )


def test_an_init_container_after_a_sidecar_counts_both(monkeypatch):
    """The sidecar is already running, so the init phase holds 64Mi + 64Mi."""
    assert _run(_init_phase(("sidecar", "init")), monkeypatch) == 0


def test_an_init_container_before_the_sidecar_counts_only_itself(monkeypatch, capsys):
    """Nothing has started beside it, so 128Mi of volume outruns the budget."""
    assert _run(_init_phase(("init", "sidecar")), monkeypatch) == 1
    assert "72Mi" in capsys.readouterr().err


def test_an_unreadable_sizelimit_is_reported(monkeypatch, capsys):
    assert _run(deployment(size_limit="64 Mi"), monkeypatch) == 1
    assert "not a readable quantity" in capsys.readouterr().err


def _pod(containers: str, init_containers: str = "", volumes: str = "") -> str:
    return (
        "apiVersion: v1\n"
        "kind: Pod\n"
        "metadata: {name: demo, namespace: apps}\n"
        "spec:\n"
        + (init_containers and "  initContainers:\n" + init_containers)
        + "  containers:\n" + containers
        + (volumes and "  volumes:\n" + volumes)
    )


def _container(name: str, limit: str | None, restart_policy: str | None = None) -> str:
    out = "    - name: %s\n" % name
    if restart_policy:
        out += "      restartPolicy: %s\n" % restart_policy
    if limit is not None:
        out += (
            "      resources:\n        requests:\n          ephemeral-storage: 64Mi\n"
            "        limits:\n          ephemeral-storage: %s\n" % limit
        )
    return out


def test_a_pod_where_only_one_container_declares_a_limit_fails(monkeypatch, capsys):
    """The hindsight shape: the dump sidecar's 128Mi was the whole pod budget."""
    doc = _pod(_container("api", None) + _container("dump", "128Mi") + _container("llama", None))
    assert _run(doc, monkeypatch) == 1
    err = capsys.readouterr().err
    assert "container 'dump' declares limits.ephemeral-storage" in err
    assert "containers 'api', 'llama' do not" in err
    assert "spend a budget they never funded" in err


def test_a_pod_where_every_container_declares_a_limit_passes(monkeypatch):
    doc = _pod(_container("api", "1Gi") + _container("dump", "128Mi"))
    assert _run(doc, monkeypatch) == 0


def test_a_pod_where_no_container_declares_a_limit_passes(monkeypatch):
    doc = _pod(_container("api", None) + _container("dump", None))
    assert _run(doc, monkeypatch) == 0


def test_a_sidecar_init_container_without_a_limit_is_unfunded(monkeypatch, capsys):
    doc = _pod(_container("api", "1Gi"), _container("proxy", None, "Always"))
    assert _run(doc, monkeypatch) == 1
    assert "container 'proxy' does not" in capsys.readouterr().err


def test_a_plain_init_container_is_outside_the_running_budget(monkeypatch):
    """It runs alone before the app containers, so its limit is its own phase's."""
    doc = _pod(_container("api", "1Gi"), _container("setup", None))
    assert _run(doc, monkeypatch) == 0


def test_a_plain_init_container_as_the_only_funder_is_reported(monkeypatch, capsys):
    """Its limit is the floor of the pod budget; with nothing else declared it IS the budget."""
    doc = _pod(_container("api", None) + _container("llama", None), _container("setup", "128Mi"))
    assert _run(doc, monkeypatch) == 1
    err = capsys.readouterr().err
    assert "init container 'setup' declares limits.ephemeral-storage" in err
    assert "containers 'api', 'llama' do not" in err
    assert "take it off the init container" in err


def test_a_plain_init_container_beside_funded_app_containers_passes(monkeypatch):
    doc = _pod(_container("api", "1Gi"), _container("setup", "128Mi"))
    assert _run(doc, monkeypatch) == 0


def test_the_partial_budget_finding_joins_the_sized_emptydir_findings(monkeypatch, capsys):
    doc = _pod(
        _container("api", None)
        + _container("dump", "64Mi").replace("- name: dump\n", "- name: dump\n      volumeMounts:\n        - {name: tmp, mountPath: /tmp}\n"),
        volumes="    - name: tmp\n      emptyDir:\n        sizeLimit: 512Mi\n",
    )
    assert _run(doc, monkeypatch) == 1
    err = capsys.readouterr().err
    assert "never funded" in err
    assert "evicts the pod before the volume fills" in err


def test_a_corpus_with_no_pod_spec_is_an_operator_error(monkeypatch, capsys):
    assert _run("kind: ConfigMap\nmetadata: {name: x}\n", monkeypatch) == 2
    assert "inspected 0 pod specs" in capsys.readouterr().err


def test_an_empty_corpus_is_an_operator_error(monkeypatch):
    assert _run("", monkeypatch) == 2


def test_an_unreadable_corpus_is_an_operator_error(monkeypatch, capsys):
    """A corpus the loader cannot parse exits 2 with the operator message."""
    assert _run("kind: Deployment\nmetadata: {name: x\n", monkeypatch) == 2
    assert "ERROR:" in capsys.readouterr().err


def test_byte_counts_print_in_the_units_a_manifest_writes():
    assert mod.format_bytes(2**30) == "1Gi"
    assert mod.format_bytes(3 * 2**20) == "3Mi"
    assert mod.format_bytes(2**10) == "1Ki"
    assert mod.format_bytes(1000) == "1000 bytes"


def test_quantity_parsing_covers_the_suffixes_it_claims():
    assert mod.parse_quantity("1Gi") == 2**30
    assert mod.parse_quantity("1G") == 10**9
    assert mod.parse_quantity("1024") == 1024
    assert mod.parse_quantity("1e3") == 1000
    assert mod.parse_quantity("1Qi") is None
    assert mod.parse_quantity(None) is None
