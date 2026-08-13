"""Tests for scripts/check-secretstore-scope.py."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "check_secretstore_scope",
    Path(__file__).resolve().parent.parent / "scripts" / "check-secretstore-scope.py",
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _run(stdin_text: str, monkeypatch, argv: list[str] | None = None) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    return mod.main(argv or [])


def _store(conditions: str = "") -> str:
    return f"""
---
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata: {{name: onepassword-homelab}}
spec:
{conditions}  provider:
    onepassword: {{connectHost: "http://connect:8080"}}
"""


SCOPED = _store("""  conditions:
    - namespaces: [apps]
""")
UNSCOPED = _store()

EXTERNAL_SECRET = """
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata: {name: app-secrets, namespace: apps}
spec:
  secretStoreRef: {kind: ClusterSecretStore, name: onepassword-homelab}
"""

NAMESPACES = """
---
apiVersion: v1
kind: Namespace
metadata:
  name: apps
  labels: {esweiss.com/vault: "true"}
---
apiVersion: v1
kind: Namespace
metadata: {name: other}
"""


def test_unscoped_cluster_store_fails(monkeypatch):
    assert _run(UNSCOPED + EXTERNAL_SECRET, monkeypatch) == 1


def test_scoped_store_covering_its_consumer_passes(monkeypatch):
    assert _run(SCOPED + EXTERNAL_SECRET, monkeypatch) == 0


def test_consumer_outside_the_conditions_fails(monkeypatch):
    stray = EXTERNAL_SECRET.replace("namespace: apps", "namespace: other")
    assert _run(SCOPED + stray, monkeypatch) == 1


def test_namespace_selector_condition_is_honored(monkeypatch):
    selector_store = _store("""  conditions:
    - namespaceSelector:
        matchLabels: {esweiss.com/vault: "true"}
""")
    assert _run(selector_store + NAMESPACES + EXTERNAL_SECRET, monkeypatch) == 0
    stray = EXTERNAL_SECRET.replace("namespace: apps", "namespace: other")
    assert _run(selector_store + NAMESPACES + stray, monkeypatch) == 1


def test_namespace_regex_condition_is_honored(monkeypatch):
    regex_store = _store("""  conditions:
    - namespaceRegexes: ["^app.*$"]
""")
    assert _run(regex_store + EXTERNAL_SECRET, monkeypatch) == 0


def test_cluster_external_secret_fanout_must_be_admitted(monkeypatch):
    ces = """
---
apiVersion: external-secrets.io/v1
kind: ClusterExternalSecret
metadata: {name: cloudflare-api-token}
spec:
  namespaceSelectors:
    - matchLabels: {esweiss.com/vault: "true"}
  externalSecretSpec:
    secretStoreRef: {kind: ClusterSecretStore, name: onepassword-homelab}
"""
    # `apps` carries the fan-out label and is in the conditions -> OK.
    assert _run(SCOPED + NAMESPACES + ces, monkeypatch) == 0
    # Label `other` too and it becomes a consumer the conditions do not admit.
    labelled = NAMESPACES.replace(
        "metadata: {name: other}",
        'metadata: {name: other, labels: {esweiss.com/vault: "true"}}',
    )
    assert _run(SCOPED + labelled + ces, monkeypatch) == 1


def test_namespaced_secretstore_reference_is_ignored(monkeypatch):
    """A namespaced SecretStore is already namespace-bound — not our invariant."""
    elsewhere = _store("""  conditions:
    - namespaces: [other]
""")
    local = EXTERNAL_SECRET.replace("kind: ClusterSecretStore", "kind: SecretStore")
    # The same reference as a ClusterSecretStore is a violation (`apps` is not
    # admitted)…
    assert _run(elsewhere + EXTERNAL_SECRET, monkeypatch) == 1
    # …and as a namespaced SecretStore it contributes no consumer at all.
    assert _run(elsewhere + local, monkeypatch) == 0


def test_matchexpressions_selector(monkeypatch):
    selector_store = _store("""  conditions:
    - namespaceSelector:
        matchExpressions:
          - {key: esweiss.com/vault, operator: Exists}
""")
    assert _run(selector_store + NAMESPACES + EXTERNAL_SECRET, monkeypatch) == 0


def test_unknown_store_is_a_violation(monkeypatch, capsys):
    """A reference that resolves to nothing is the runtime failure this gate exists
    to catch — the ExternalSecret never syncs and its Secret goes stale."""
    assert _run(EXTERNAL_SECRET, monkeypatch) == 1
    assert "referenced but not defined in this corpus" in capsys.readouterr().err


def test_declared_external_store_is_exempt(monkeypatch):
    """A store genuinely managed outside the linted tree is declared, not silent."""
    assert _run(EXTERNAL_SECRET, monkeypatch, ["--external-store", "onepassword-homelab"]) == 0


def test_empty_corpus_is_an_operator_error(monkeypatch, capsys):
    """A broken pipe or a wrong kustomize path must not read as a pass."""
    assert _run("", monkeypatch) == 2
    assert "empty corpus" in capsys.readouterr().err


def test_a_corpus_with_no_stores_and_no_consumers_is_an_operator_error(monkeypatch, capsys):
    """The likelier wiring failure than a literally empty pipe.

    A render loop that produced output but never reached the stage defining the
    stores leaves a large corpus with nothing this gate can inspect.
    """
    unrelated = """
---
apiVersion: v1
kind: ConfigMap
metadata: {name: settings, namespace: apps}
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: app, namespace: apps}
"""
    assert _run(unrelated, monkeypatch) == 2
    assert "inspected 0 ClusterSecretStores" in capsys.readouterr().err


def test_unparseable_corpus_is_an_operator_error(monkeypatch, capsys):
    assert _run("a: [1\n", monkeypatch) == 2
    assert "Failed to parse YAML input" in capsys.readouterr().err


def test_cluster_external_secret_literal_namespaces_are_consumers(monkeypatch):
    """ESO unions `spec.namespaces` with the selectors.

    A CES written with the literal list alone matched no selector, so it used to
    contribute zero consumers and its fan-out went unchecked entirely.
    """
    ces = """
---
apiVersion: external-secrets.io/v1
kind: ClusterExternalSecret
metadata: {name: literal-fan-out}
spec:
  namespaces: [other]
  externalSecretSpec:
    secretStoreRef: {kind: ClusterSecretStore, name: onepassword-homelab}
"""
    # `other` is not admitted by SCOPED (which names `apps` only).
    assert _run(SCOPED + NAMESPACES + ces, monkeypatch) == 1
    assert _run(SCOPED + NAMESPACES + ces.replace("[other]", "[apps]"), monkeypatch) == 0


def test_empty_namespace_selector_fans_out_to_every_namespace(monkeypatch):
    """`namespaceSelector: {}` is a selector with no terms — it matches EVERY
    namespace, so the widest fan-out is exactly the shape that must be checked."""
    ces = """
---
apiVersion: external-secrets.io/v1
kind: ClusterExternalSecret
metadata: {name: fan-out-everywhere}
spec:
  namespaceSelector: {}
  externalSecretSpec:
    secretStoreRef: {kind: ClusterSecretStore, name: onepassword-homelab}
"""
    # `other` is not in the store's conditions, and an empty selector reaches it.
    assert _run(SCOPED + NAMESPACES + ces, monkeypatch) == 1
    # Widen the store to both namespaces and the same manifest passes.
    both = _store("""  conditions:
    - namespaces: [apps, other]
""")
    assert _run(both + NAMESPACES + ces, monkeypatch) == 0
