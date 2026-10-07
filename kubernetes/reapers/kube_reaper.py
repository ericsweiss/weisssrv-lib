#!/usr/bin/env python3
"""Shared half of a pod-reaper CronJob program.

Holds the apiserver client, paging, age arithmetic, deletes and entry point; a
reaper supplies the selector and guards. Stdlib only. Contract: docs/SCRIPTS.md.
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Iterator, Optional, Set
from urllib.parse import urlencode

API = "https://kubernetes.default.svc"
SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


class KubeApi:
    """kube-apiserver client on the pod's ServiceAccount credentials."""

    def __init__(self, token: str, ca_file: str, timeout: int,
                 api_base: str = API) -> None:
        self._headers = {"Authorization": "Bearer %s" % token}
        self._ctx = ssl.create_default_context(cafile=ca_file)
        self._timeout = timeout
        self._api = api_base

    @classmethod
    def from_service_account(cls, timeout: int, sa_dir: str = SA_DIR,
                             api_base: str = API) -> "KubeApi":
        with open("%s/token" % sa_dir) as fh:
            token = fh.read().strip()
        return cls(token, "%s/ca.crt" % sa_dir, timeout, api_base)

    def request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        headers = dict(self._headers)
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self._api + path, method=method, headers=headers, data=data
        )
        with urllib.request.urlopen(req, timeout=self._timeout, context=self._ctx) as r:
            resp = r.read().decode()
        return json.loads(resp) if resp else {}


def paged(api, path: str, params: dict, page: int) -> Iterator[dict]:
    """Yield each list response, following the continue token.

    Paged so a large backlog cannot exhaust the container's memory limit.
    """
    params = dict(params, limit=str(page))
    while True:
        resp = api.request("GET", "%s?%s" % (path, urlencode(params)))
        yield resp
        cont = resp.get("metadata", {}).get("continue")
        if not cont:
            return
        params["continue"] = cont


def parse_ts(ts: str) -> datetime:
    # K8s RFC3339 ends in 'Z'; fromisoformat needs +00:00.
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def creation_age_minutes(obj: dict, now: datetime) -> Optional[int]:
    """Age from creationTimestamp; unparseable or absent -> None (KEEP)."""
    ts = obj.get("metadata", {}).get("creationTimestamp")
    if not ts:
        return None
    try:
        return int((now - parse_ts(ts)).total_seconds() // 60)
    except (TypeError, ValueError):
        return None


def terminal_age_minutes(pod: dict, now: datetime) -> Optional[int]:
    """Age from the newest terminated.finishedAt across all container statuses.

    An unparseable finishedAt keeps the pod when the container started and is
    skipped when it never did. There is no startTime fallback.
    """
    status = pod.get("status", {})
    statuses = (status.get("initContainerStatuses", [])
                + status.get("containerStatuses", [])
                + status.get("ephemeralContainerStatuses", []))
    finished = []
    for cs in statuses:
        term = (cs.get("state", {}) or {}).get("terminated") or {}
        finished_at = term.get("finishedAt")
        if not finished_at:
            if cs.get("containerID"):
                return None
            continue
        try:
            finished.append(parse_ts(finished_at))
        except (TypeError, ValueError):
            return None
    if not finished:
        return None
    return int((now - max(finished)).total_seconds() // 60)


def delete_pod(api, ns: str, name: str, uid: str) -> None:
    # uid precondition: the API rejects (409) the delete if this name now refers
    # to a different, newer pod after a stale list.
    api.request("DELETE", "/api/v1/namespaces/%s/pods/%s" % (ns, name),
                {"apiVersion": "v1", "kind": "DeleteOptions",
                 "gracePeriodSeconds": 0,
                 "preconditions": {"uid": uid}})


def delete_secret(api, ns: str, name: str, uid: str) -> None:
    api.request("DELETE", "/api/v1/namespaces/%s/secrets/%s" % (ns, name),
                {"apiVersion": "v1", "kind": "DeleteOptions",
                 "preconditions": {"uid": uid}})


def is_replicaset_owned(pod: dict) -> bool:
    return any(
        owner.get("kind") == "ReplicaSet"
        for owner in pod.get("metadata", {}).get("ownerReferences") or []
    )


def live_pod_refs(api, ns: str, page: int, over_budget):
    """Return (live pod UIDs, imagePullSecrets names), or None.

    CRITICAL: None means the budget ran out with pages unread, so the set is
    incomplete and must never feed deletions, or a sweep takes live credentials.
    """
    uids: Set[str] = set()
    pull_secrets: Set[str] = set()
    for resp in paged(api, "/api/v1/namespaces/%s/pods" % ns, {}, page):
        for pod in resp.get("items", []):
            uid = pod.get("metadata", {}).get("uid")
            if uid:
                uids.add(uid)
            for ref in pod.get("spec", {}).get("imagePullSecrets") or []:
                if ref.get("name"):
                    pull_secrets.add(ref["name"])
        # Returning here leaves the generator suspended, so the next page is
        # never requested.
        if resp.get("metadata", {}).get("continue") and over_budget():
            return None
    return uids, pull_secrets


def require_positive(values) -> None:
    """Fail closed on nonsense knobs: a negative age makes every terminal object
    eligible immediately, and a zero page or period disables a bound."""
    invalid = {name: value for name, value in values if value <= 0}
    if invalid:
        details = ", ".join("%s=%s" % (k, v) for k, v in sorted(invalid.items()))
        raise SystemExit(
            "invalid reaper configuration; values must be positive: %s" % details
        )


def delete_with_logging(delete, api, ns: str, name: str, uid: str, note: str,
                        log=print) -> bool:
    """-> True when the object is gone or was already gone, False on an error
    that should fail the Job. A 404 is already gone; a 409 is a lost uid
    precondition. Both are no-ops, not faults."""
    try:
        delete(api, ns, name, uid)
        log("DELETE %s/%s (%s)" % (ns, name, note), flush=True)
        return True
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 409):
            log("GONE   %s/%s (already deleted or replaced)" % (ns, name), flush=True)
            return True
        log("ERROR delete %s/%s: HTTP %d" % (ns, name, exc.code), flush=True)
        return False
    except urllib.error.URLError as exc:
        log("ERROR delete %s/%s: %s" % (ns, name, exc), flush=True)
        return False


def rotate(items, now: datetime, period_seconds: int):
    """Start each scheduled run at a different offset, so a budget stop does not
    always strand the same tail. Derived from the clock with no persisted state,
    so period_seconds must equal the CronJob's schedule period."""
    if not items:
        return items
    offset = int(now.timestamp() // period_seconds) % len(items)
    return items[offset:] + items[:offset]


def run_reaper(load_config, run, api_factory=KubeApi.from_service_account) -> int:
    """The entry point every reaper shares: load config, build the client, start
    the budget clock, hand `run` a now and an over-budget predicate."""
    cfg = load_config()
    api = api_factory(cfg.api_timeout_seconds)
    start = time.monotonic()
    return run(
        api,
        cfg,
        datetime.now(timezone.utc),
        lambda: time.monotonic() - start > cfg.budget_seconds,
    )
