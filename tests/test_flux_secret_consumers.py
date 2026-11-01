#!/usr/bin/env python3
"""Unit tests for scripts/flux-secret-consumers.py.

Each test feeds the helper a throwaway `kubectl get -o json` payload on stdin
and asserts the TSV it emits, or the exit code it refuses with.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from script_loader import SCRIPTS, load_script

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


@pytest.mark.parametrize("key", ["initContainers", "ephemeralContainers"])
def test_a_reference_from_any_container_list_is_found(key):
    workload = _workload("app", env_from=["tls"])
    pod = workload["spec"]["template"]["spec"]
    pod[key] = pod.pop("containers")
    pod["containers"] = [{"name": "main"}]
    rows = flux_secret_consumers.consumers([workload], "tls")
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
    assert "cannot be written as a bounded `-l` selector" in result.stderr


def test_a_consumer_with_an_unreadable_expression_is_refused():
    workload = _workload("app", volumes=["tls"])
    workload["spec"]["selector"]["matchExpressions"] = [
        {"key": "tier", "operator": "Gt", "values": ["1"]}
    ]
    result = _run({"items": [workload]}, "tls")
    assert result.returncode == 2
    assert "a matchExpressions term kubectl cannot take" in result.stderr


def test_every_workload_kind_is_listed_by_its_lowercased_kind():
    result = _run(
        {"items": [_workload("b", kind="StatefulSet", volumes=["tls"]),
                   _workload("c", kind="DaemonSet", env_from=["tls"])]},
        "tls",
    )
    assert result.returncode == 0, result.stderr
    assert [row.split("\t")[0] for row in result.stdout.splitlines()] == [
        "statefulset/b", "daemonset/c",
    ]


def test_no_consumer_prints_nothing_and_exits_0():
    result = _run({"items": [_workload("a", volumes=["other"])]}, "tls")
    assert result.returncode == 0 and result.stdout == ""


def test_match_expressions_join_the_selector_in_kubectl_syntax():
    workload = _workload("app", volumes=["tls"])
    workload["spec"]["selector"]["matchExpressions"] = [
        {"key": "tier", "operator": "In", "values": ["web", "api"]},
        {"key": "canary", "operator": "NotIn", "values": ["true"]},
        {"key": "app.kubernetes.io/part-of", "operator": "Exists"},
        {"key": "legacy", "operator": "DoesNotExist"},
    ]
    rows = flux_secret_consumers.consumers([workload], "tls")
    assert rows[0][2] == (
        "app.kubernetes.io/name=app,tier in (web,api),canary notin (true),"
        "app.kubernetes.io/part-of,!legacy"
    )


@pytest.mark.parametrize(
    "expression",
    [{"key": "tier", "operator": "Gt", "values": ["1"]}, {"key": "tier", "operator": "In"}, "tier"],
    ids=["unknown-operator", "in-without-values", "not-a-mapping"],
)
def test_an_unreadable_expression_makes_the_consumer_unscopable(expression):
    workload = _workload("app", volumes=["tls"])
    workload["spec"]["selector"]["matchExpressions"] = [expression]
    result = _run({"items": [workload]}, "tls")
    assert result.returncode == 2
    assert "deployment/app" in result.stderr


def test_match_expressions_alone_scope_the_consumer():
    workload = _workload("app", volumes=["tls"], match_labels={})
    workload["spec"]["selector"]["matchExpressions"] = [
        {"key": "app", "operator": "In", "values": ["a"]}
    ]
    result = _run({"items": [workload]}, "tls")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "deployment/app\tother\tapp in (a)\n"


@pytest.mark.parametrize(
    "expressions",
    [
        [{"key": "canary", "operator": "DoesNotExist"}],
        [{"key": "app", "operator": "Exists"}],
        [{"key": "tier", "operator": "NotIn", "values": ["web"]},
         {"key": "app", "operator": "Exists"}],
    ],
    ids=["does-not-exist-only", "exists-only", "notin-and-exists"],
)
def test_a_selector_without_a_positive_term_is_unscopable(expressions):
    """Negative and Exists terms match every pod lacking a label, so a delete
    on them alone reaches other workloads' pods in the namespace."""
    workload = _workload("app", volumes=["tls"], match_labels={})
    workload["spec"]["selector"]["matchExpressions"] = expressions
    result = _run({"items": [workload]}, "tls")
    assert result.returncode == 2
    assert "deployment/app" in result.stderr and result.stdout == ""


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
