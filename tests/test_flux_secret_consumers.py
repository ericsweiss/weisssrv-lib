#!/usr/bin/env python3
"""Unit tests for scripts/flux-secret-consumers.py.

Each test feeds the helper a throwaway `kubectl get -o json` payload on stdin
and asserts the TSV it emits, or the exit code it refuses with.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from script_loader import SCRIPTS, load_script  # noqa: E402

HELPER = SCRIPTS / "flux-secret-consumers.py"
flux_secret_consumers = load_script("flux-secret-consumers.py")


def _workload(name, kind="Deployment", managed=False, volumes=None, env_from=None,
              env=None, pull=None, projected=None, match_labels=None):
    labels = {"kustomize.toolkit.fluxcd.io/name": "apps"} if managed else {}
    selector = {"matchLabels": match_labels} if match_labels is not None else {
        "matchLabels": {"app.kubernetes.io/name": name}
    }
    container = {"name": name}
    if env_from:
        container["envFrom"] = [{"secretRef": {"name": s}} for s in env_from]
    if env:
        container["env"] = [
            {"name": "X", "valueFrom": {"secretKeyRef": {"name": s, "key": "k"}}}
            for s in env
        ]
    pod_spec = {"containers": [container]}
    if volumes:
        pod_spec["volumes"] = [{"name": s, "secret": {"secretName": s}} for s in volumes]
    if projected:
        pod_spec.setdefault("volumes", []).append(
            {"name": "p", "projected": {"sources": [{"secret": {"name": s}} for s in projected]}}
        )
    if pull:
        pod_spec["imagePullSecrets"] = [{"name": s} for s in pull]
    return {
        "kind": kind,
        "metadata": {"name": name, "labels": labels},
        "spec": {"selector": selector, "template": {"spec": pod_spec}},
    }


def _run(payload, *args):
    return subprocess.run(
        [sys.executable, str(HELPER), *args],
        input=json.dumps(payload), capture_output=True, text=True,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"volumes": ["tls"]},
        {"env_from": ["tls"]},
        {"env": ["tls"]},
        {"pull": ["tls"]},
        {"projected": ["tls"]},
    ],
    ids=["volume", "envFrom", "env", "imagePullSecret", "projected"],
)
def test_every_reference_mechanism_is_found(kwargs):
    """A Secret reached through any one of these is a consumer; a reader that
    knows only volumes leaves the workload out of a restart."""
    rows = flux_secret_consumers.consumers([_workload("app", **kwargs)], "tls")
    assert rows == [("deployment/app", "other", "app.kubernetes.io/name=app")]


def test_an_unreferenced_secret_matches_nothing():
    rows = flux_secret_consumers.consumers([_workload("app", volumes=["other"])], "tls")
    assert rows == []


def test_a_kustomize_managed_workload_is_labelled_as_such():
    """A restart annotation on a kustomize-managed workload is drift the next
    reconcile reverts, so the caller must be told which manager owns it."""
    rows = flux_secret_consumers.consumers(
        [_workload("app", managed=True, volumes=["tls"])], "tls"
    )
    assert rows[0][1] == "kustomize"


def test_the_cli_prints_one_tsv_row_per_consumer():
    result = _run(
        {"items": [_workload("a", volumes=["tls"]), _workload("b", env=["tls"])]}, "tls"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "deployment/a\tother\tapp.kubernetes.io/name=a",
        "deployment/b\tother\tapp.kubernetes.io/name=b",
    ]


def test_a_consumer_without_matchlabels_is_refused():
    """An unscopable workload cannot be restarted by a label-selected pod
    delete, and a silent omission would leave it running the old Secret."""
    result = _run({"items": [_workload("app", volumes=["tls"], match_labels={})]}, "tls")
    assert result.returncode == 2
    assert "no matchLabels" in result.stderr


def test_a_payload_without_items_is_an_operator_error():
    result = _run({"kind": "Deployment"}, "tls")
    assert result.returncode == 2
    assert "no .items" in result.stderr


def test_unparseable_stdin_is_an_operator_error():
    result = subprocess.run(
        [sys.executable, str(HELPER), "tls"],
        input="not json", capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "not the JSON kubectl emits" in result.stderr


def test_the_secret_name_is_required():
    result = _run({"items": []})
    assert result.returncode == 2
    assert "usage:" in result.stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
