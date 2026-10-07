"""kubernetes/reapers/kube_reaper.py — the shared reaper half."""
from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from script_loader import REPO, load_path

kube_reaper = load_path(REPO / "kubernetes" / "reapers" / "kube_reaper.py")

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class FakeApi:
    """Records requests and replays canned list responses."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET":
            return self.responses.pop(0) if self.responses else {}
        return {}


# --- paging --------------------------------------------------------------

def test_paging_follows_the_continue_token():
    api = FakeApi([
        {"items": [1], "metadata": {"continue": "tok"}},
        {"items": [2], "metadata": {}},
    ])
    pages = list(kube_reaper.paged(api, "/api/v1/pods", {"labelSelector": "a/b"}, 25))
    assert [p["items"] for p in pages] == [[1], [2]]
    assert "continue=tok" in api.calls[1][1]


def test_a_label_selector_is_url_encoded():
    api = FakeApi([{"items": [], "metadata": {}}])
    list(kube_reaper.paged(api, "/api/v1/pods", {"labelSelector": "ex.com/x=1"}, 5))
    assert "ex.com%2Fx%3D1" in api.calls[0][1]


# --- ages ----------------------------------------------------------------

def test_creation_age_is_in_minutes():
    pod = {"metadata": {"creationTimestamp": "2026-01-01T11:30:00Z"}}
    assert kube_reaper.creation_age_minutes(pod, NOW) == 30


def test_an_absent_creation_timestamp_keeps_the_object():
    assert kube_reaper.creation_age_minutes({"metadata": {}}, NOW) is None


def test_terminal_age_uses_the_newest_finished_at():
    pod = {"status": {"containerStatuses": [
        {"state": {"terminated": {"finishedAt": "2026-01-01T11:00:00Z"}}},
        {"state": {"terminated": {"finishedAt": "2026-01-01T11:45:00Z"}}},
    ]}}
    assert kube_reaper.terminal_age_minutes(pod, NOW) == 15


def test_a_started_container_with_no_finished_at_keeps_the_pod():
    pod = {"status": {"containerStatuses": [{"containerID": "docker://x", "state": {}}]}}
    assert kube_reaper.terminal_age_minutes(pod, NOW) is None


def test_a_container_that_never_started_does_not_block_reaping():
    pod = {"status": {"initContainerStatuses": [
        {"state": {"terminated": {"finishedAt": "2026-01-01T11:00:00Z"}}}],
        "containerStatuses": [{"state": {}}]}}
    assert kube_reaper.terminal_age_minutes(pod, NOW) == 60


def test_terminal_age_never_falls_back_to_creation_time():
    """A freshly-terminal long job would otherwise be deleted immediately."""
    pod = {"metadata": {"creationTimestamp": "2026-01-01T00:00:00Z"}, "status": {}}
    assert kube_reaper.terminal_age_minutes(pod, NOW) is None


# --- deletes -------------------------------------------------------------

def test_a_pod_delete_carries_the_uid_precondition():
    api = FakeApi()
    kube_reaper.delete_pod(api, "ns", "pod-1", "uid-1")
    method, path, body = api.calls[0]
    assert (method, path) == ("DELETE", "/api/v1/namespaces/ns/pods/pod-1")
    assert body["preconditions"] == {"uid": "uid-1"}
    assert body["gracePeriodSeconds"] == 0


def test_a_secret_delete_carries_the_uid_precondition():
    api = FakeApi()
    kube_reaper.delete_secret(api, "ns", "s-1", "uid-1")
    assert api.calls[0][2]["preconditions"] == {"uid": "uid-1"}


@pytest.mark.parametrize("code", [404, 409])
def test_an_already_gone_delete_is_not_an_error(code):
    def delete(*_args):
        raise urllib.error.HTTPError("u", code, "gone", {}, None)
    lines = []
    assert kube_reaper.delete_with_logging(
        delete, None, "ns", "p", "u", "note", log=lambda *a, **k: lines.append(a[0])
    ) is True
    assert "GONE" in lines[0]


def test_a_server_error_delete_is_a_fault():
    def delete(*_args):
        raise urllib.error.HTTPError("u", 500, "boom", {}, None)
    assert kube_reaper.delete_with_logging(
        delete, None, "ns", "p", "u", "note", log=lambda *a, **k: None
    ) is False


def test_a_successful_delete_logs_and_reports_true():
    calls = []

    def delete(*args):
        calls.append(args)

    lines = []
    assert kube_reaper.delete_with_logging(
        delete, None, "ns", "p", "u", "note", log=lambda *a, **k: lines.append(a[0])
    ) is True
    assert calls == [(None, "ns", "p", "u")]
    assert lines[0].startswith("DELETE ns/p")


def test_an_unreachable_apiserver_is_a_fault():
    def delete(*_args):
        raise urllib.error.URLError("down")

    lines = []
    assert kube_reaper.delete_with_logging(
        delete, None, "ns", "p", "u", "note", log=lambda *a, **k: lines.append(a[0])
    ) is False
    assert "ERROR delete" in lines[0]


# --- the apiserver client -------------------------------------------------

class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class TestKubeApi:
    """The only place the HTTP method, body and headers reach the wire."""

    def _capture(self, monkeypatch, body: bytes = b'{"items": []}'):
        seen = {}

        def fake_urlopen(req, timeout=None, context=None):
            seen["req"] = req
            seen["timeout"] = timeout
            return _FakeResponse(body)

        monkeypatch.setattr(kube_reaper.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(kube_reaper.ssl, "create_default_context",
                            lambda cafile=None: "ctx")
        return seen

    def test_a_get_carries_the_bearer_token_and_no_body(self, monkeypatch):
        seen = self._capture(monkeypatch)
        api = kube_reaper.KubeApi("tok", "/dev/null", 5, api_base="https://api.invalid")
        assert api.request("GET", "/api/v1/pods") == {"items": []}
        req = seen["req"]
        assert req.get_method() == "GET"
        assert req.headers["Authorization"] == "Bearer tok"
        assert "Content-type" not in req.headers
        assert req.data is None

    def test_a_body_is_sent_as_json(self, monkeypatch):
        seen = self._capture(monkeypatch)
        api = kube_reaper.KubeApi("tok", "/dev/null", 5, api_base="https://api.invalid")
        api.request("DELETE", "/api/v1/pods/p", {"preconditions": {"uid": "u"}})
        req = seen["req"]
        assert req.get_method() == "DELETE"
        assert req.headers["Content-type"] == "application/json"
        assert json.loads(req.data.decode()) == {"preconditions": {"uid": "u"}}

    def test_an_empty_response_body_is_an_empty_mapping(self, monkeypatch):
        self._capture(monkeypatch, body=b"")
        api = kube_reaper.KubeApi("tok", "/dev/null", 5, api_base="https://api.invalid")
        assert api.request("DELETE", "/api/v1/pods/p") == {}

    def test_from_service_account_reads_the_mounted_token(self, monkeypatch, tmp_path):
        (tmp_path / "token").write_text("mounted-token\n", encoding="utf-8")
        (tmp_path / "ca.crt").write_text("", encoding="utf-8")
        seen = self._capture(monkeypatch)
        api = kube_reaper.KubeApi.from_service_account(
            5, sa_dir=str(tmp_path), api_base="https://api.invalid"
        )
        api.request("GET", "/api/v1/pods")
        assert seen["req"].headers["Authorization"] == "Bearer mounted-token"


# --- guards and bookkeeping ---------------------------------------------

def test_replicaset_ownership_is_detected():
    assert kube_reaper.is_replicaset_owned(
        {"metadata": {"ownerReferences": [{"kind": "ReplicaSet", "uid": "u"}]}})
    assert not kube_reaper.is_replicaset_owned(
        {"metadata": {"ownerReferences": [{"kind": "Job", "uid": "u"}]}})


def test_a_budget_stop_makes_the_live_ref_set_unusable():
    """A partial set would make an in-flight job's Secret look unreferenced."""
    api = FakeApi([{"items": [], "metadata": {"continue": "tok"}}])
    assert kube_reaper.live_pod_refs(api, "ns", 5, lambda: True) is None


def test_a_complete_ref_set_is_returned():
    api = FakeApi([{"items": [
        {"metadata": {"uid": "u1"}, "spec": {"imagePullSecrets": [{"name": "s1"}]}},
    ], "metadata": {}}])
    assert kube_reaper.live_pod_refs(api, "ns", 5, lambda: True) == ({"u1"}, {"s1"})


def test_a_non_positive_knob_refuses_to_run():
    with pytest.raises(SystemExit) as exc:
        kube_reaper.require_positive([("MAX_AGE_MINUTES", 0), ("PAGE_LIMIT", 25)])
    assert "MAX_AGE_MINUTES=0" in str(exc.value)
    kube_reaper.require_positive([("MAX_AGE_MINUTES", 30)])


def test_rotation_moves_the_starting_namespace_each_period():
    later = NOW + timedelta(seconds=900)
    first = kube_reaper.rotate(["a", "b", "c"], NOW, 900)
    assert sorted(first) == ["a", "b", "c"]
    assert kube_reaper.rotate(["a", "b", "c"], later, 900) != first


def test_rotation_of_an_empty_list_is_empty():
    assert kube_reaper.rotate([], NOW, 900) == []


def test_run_reaper_hands_run_a_working_over_budget_predicate():
    class Cfg:
        api_timeout_seconds = 5
        budget_seconds = 0

    seen = {}

    def run(api, cfg, now, over_budget):
        seen["over_budget"] = over_budget()
        seen["now"] = now
        return 0

    assert kube_reaper.run_reaper(lambda: Cfg(), run, api_factory=lambda t: "api") == 0
    assert seen["over_budget"] is True
    assert seen["now"].tzinfo is timezone.utc


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
