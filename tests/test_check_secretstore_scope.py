"""Tests for scripts/check-secretstore-scope.py."""
from __future__ import annotations

import io

from script_loader import load_script

mod = load_script("check-secretstore-scope.py")


def _run(stdin_text: str, monkeypatch, argv: list[str] | None = None) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    try:
        return mod.main(argv or [])
    except SystemExit as exc:  # the shared corpus loader exits 2 directly
        return int(exc.code)


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
  labels: {example.test/vault: "true"}
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
        matchLabels: {example.test/vault: "true"}
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
    - matchLabels: {example.test/vault: "true"}
  externalSecretSpec:
    secretStoreRef: {kind: ClusterSecretStore, name: onepassword-homelab}
"""
    # `apps` carries the fan-out label and is in the conditions -> OK.
    assert _run(SCOPED + NAMESPACES + ces, monkeypatch) == 0
    # Label `other` too and it becomes a consumer the conditions do not admit.
    labelled = NAMESPACES.replace(
        "metadata: {name: other}",
        'metadata: {name: other, labels: {example.test/vault: "true"}}',
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
          - {key: example.test/vault, operator: Exists}
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
    assert "failed to parse YAML input" in capsys.readouterr().err


def test_cluster_external_secret_literal_namespaces_are_consumers(monkeypatch):
    """ESO unions `spec.namespaces` with the selectors, so a literal list is a
    consumer too."""
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


# --- a condition that admits everything is not a scope ------------------------


def test_an_empty_namespace_selector_condition_is_not_a_scope(monkeypatch, capsys):
    """`namespaceSelector: {}` has no terms, so it matches every namespace —
    exactly as wide as declaring no conditions at all."""
    catch_all = _store("""  conditions:
    - namespaceSelector: {}
""")
    assert _run(catch_all + NAMESPACES + EXTERNAL_SECRET, monkeypatch) == 1
    assert "admits every namespace" in capsys.readouterr().err


def test_an_empty_matchlabels_condition_is_not_a_scope(monkeypatch):
    catch_all = _store("""  conditions:
    - namespaceSelector: {matchLabels: {}}
""")
    assert _run(catch_all + NAMESPACES + EXTERNAL_SECRET, monkeypatch) == 1


def test_a_catch_all_namespace_regex_is_not_a_scope(monkeypatch, capsys):
    """`.*` matches every namespace; so does an unanchored `.+` or an empty
    pattern, because the matcher uses re.search."""
    for pattern in ('".*"', '".+"', '"^.*$"', '""'):
        catch_all = _store(f"""  conditions:
    - namespaceRegexes: [{pattern}]
""")
        assert _run(catch_all + EXTERNAL_SECRET, monkeypatch) == 1, pattern
        capsys.readouterr()


def test_an_anchored_namespace_regex_is_still_a_scope(monkeypatch):
    """The positive case the catch-all probe must not swallow."""
    scoped = _store("""  conditions:
    - namespaceRegexes: ["^apps$"]
""")
    assert _run(scoped + EXTERNAL_SECRET, monkeypatch) == 0


def test_an_unparseable_namespace_regex_is_an_operator_error(monkeypatch, capsys):
    """A pattern the API would reject is a defect the gate must name, not swallow."""
    broken = _store("""  conditions:
    - namespaceRegexes: ["^app("]
""")
    assert _run(broken + EXTERNAL_SECRET, monkeypatch) == 2
    assert "does not compile" in capsys.readouterr().err


def test_an_unparseable_catch_all_regex_is_not_silently_scoped(monkeypatch, capsys):
    """The fail-open direction: a broken pattern must never read as scoped."""
    broken = _store("""  conditions:
    - namespaceRegexes: ["(.*"]
""")
    assert _run(broken + EXTERNAL_SECRET, monkeypatch) == 2
    assert "does not compile" in capsys.readouterr().err


# --- the selector matcher fails CLOSED on a shape the apiserver rejects -------


def test_an_unknown_match_expression_operator_never_admits(monkeypatch):
    """A misspelled operator matched no arm and fell through to "admits"."""
    for operator in ("in", "Equals", ""):
        bad = _store(f"""  conditions:
    - namespaceSelector:
        matchExpressions:
          - {{key: example.test/vault, operator: "{operator}", values: ["true"]}}
""")
        assert _run(bad + NAMESPACES + EXTERNAL_SECRET, monkeypatch) == 1, operator


def test_a_string_values_match_expression_never_admits(monkeypatch):
    """`values: "true"` would make the membership test a substring test."""
    bad = _store("""  conditions:
    - namespaceSelector:
        matchExpressions:
          - {key: example.test/vault, operator: In, values: "true"}
""")
    assert _run(bad + NAMESPACES + EXTERNAL_SECRET, monkeypatch) == 1


def test_an_unknown_match_expression_key_never_admits(monkeypatch):
    bad = _store("""  conditions:
    - namespaceSelector:
        matchExpressions:
          - {key: example.test/vault, operator: Exists, valuse: []}
""")
    assert _run(bad + NAMESPACES + EXTERNAL_SECRET, monkeypatch) == 1


def test_a_valid_match_expression_still_admits(monkeypatch):
    ok = _store("""  conditions:
    - namespaceSelector:
        matchExpressions:
          - {key: example.test/vault, operator: In, values: ["true"]}
""")
    assert _run(ok + NAMESPACES + EXTERNAL_SECRET, monkeypatch) == 0


def test_an_unused_external_store_is_reported_not_fatal(monkeypatch, capsys):
    argv = ["--external-store", "gone-elsewhere"]
    assert _run(SCOPED + EXTERNAL_SECRET, monkeypatch, argv) == 0
    assert "not referenced by this corpus: gone-elsewhere" in capsys.readouterr().out


# --- a selector key the CRD prunes is an unmodelled grant, not a non-match ----


def test_a_misspelled_selector_key_is_reported_as_unmodelled(monkeypatch, capsys):
    """`matchLabel:` (singular) is pruned, leaving the empty selector."""
    typo = _store("""  conditions:
    - namespaceSelector:
        matchLabel: {example.test/vault: "true"}
""")
    assert _run(typo + NAMESPACES + EXTERNAL_SECRET, monkeypatch) == 1
    err = capsys.readouterr().err
    assert "unmodelled key(s) matchLabel" in err
    assert "admits every namespace" in err


def test_an_extra_sibling_selector_key_is_reported_as_unmodelled(monkeypatch, capsys):
    """A recognised term beside an unrecognised one is still pruned to nothing
    this gate can judge."""
    extra = _store("""  conditions:
    - namespaceSelector:
        matchLabels: {example.test/vault: "true"}
        matchFields: {metadata.name: apps}
""")
    assert _run(extra + NAMESPACES + EXTERNAL_SECRET, monkeypatch) == 1
    assert "unmodelled key(s) matchFields" in capsys.readouterr().err


def test_a_cluster_external_secret_selector_typo_is_unmodelled(monkeypatch, capsys):
    """The fan-out side of the same prune: it reaches every namespace."""
    ces = """
---
apiVersion: external-secrets.io/v1
kind: ClusterExternalSecret
metadata: {name: fan-out-typo}
spec:
  namespaceSelectors:
    - matchLables: {example.test/vault: "true"}
  externalSecretSpec:
    secretStoreRef: {kind: ClusterSecretStore, name: onepassword-homelab}
"""
    assert _run(SCOPED + NAMESPACES + ces, monkeypatch) == 1
    assert "unmodelled key(s) matchLables" in capsys.readouterr().err
