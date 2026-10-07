#!/usr/bin/env python3
"""Assert every cert-manager issuer reference resolves inside the corpus.

A Certificate naming an issuer the cluster does not ship stays Pending with no
Secret. Contract: weisssrv-lib docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from gate_common import (  # noqa: E402  (resolved from this script's own directory)
        OperatorError,
        doc_key,
        doc_namespace,
        load_corpus,
    )
except ImportError:
    print(
        "ERROR: gate_common.py must be vendored beside this gate "
        "(see scripts/vendorable-paths.yml)", file=sys.stderr,
    )
    raise SystemExit(2) from None

CERT_MANAGER_GROUP = "cert-manager.io"
CLUSTER_ISSUER = "ClusterIssuer"
ISSUER = "Issuer"
# cert-manager's ingress-shim reads these; exactly one of the first two is set.
CLUSTER_ISSUER_ANNOTATION = "cert-manager.io/cluster-issuer"
ISSUER_ANNOTATION = "cert-manager.io/issuer"
ISSUER_GROUP_ANNOTATION = "cert-manager.io/issuer-group"


def parse_exempt(values: list[str]) -> dict[str, str]:
    """`NAME=REASON` pairs for issuers installed outside this corpus."""
    exempt: dict[str, str] = {}
    for raw in values or []:
        name, sep, reason = raw.partition("=")
        if not sep or not name.strip() or not reason.strip():
            raise OperatorError(f"--allow-external takes NAME=REASON, got {raw!r}")
        exempt[name.strip()] = reason.strip()
    return exempt


def _issuer_refs(doc: dict) -> list[tuple[str, str, str]]:
    """(kind, name, where) for every cert-manager issuer this document names.

    A reference whose group is another issuer implementation is left out: its
    object is not a cert-manager Issuer and would never be in this corpus.
    """
    refs: list[tuple[str, str, str]] = []
    where = doc_key(doc)
    ref = (doc.get("spec") or {}).get("issuerRef")
    if isinstance(ref, dict) and ref.get("name"):
        group = str(ref.get("group") or CERT_MANAGER_GROUP)
        if group == CERT_MANAGER_GROUP:
            # cert-manager defaults an omitted kind to Issuer, not ClusterIssuer.
            refs.append((str(ref.get("kind") or ISSUER), str(ref["name"]),
                         f"{where} spec.issuerRef"))
    annotations = (doc.get("metadata") or {}).get("annotations") or {}
    if not isinstance(annotations, dict):
        return refs
    group = str(annotations.get(ISSUER_GROUP_ANNOTATION) or CERT_MANAGER_GROUP)
    if group != CERT_MANAGER_GROUP:
        return refs
    for annotation, kind in ((CLUSTER_ISSUER_ANNOTATION, CLUSTER_ISSUER),
                             (ISSUER_ANNOTATION, ISSUER)):
        name = annotations.get(annotation)
        if isinstance(name, str) and name.strip():
            refs.append((kind, name.strip(), f"{where} {annotation}"))
    return refs


def violations(docs: list[dict], exempt: dict[str, str]) -> tuple[list[str], int]:
    """-> (violations, references inspected). The count feeds the vacuity guard."""
    cluster_issuers = {
        (d.get("metadata") or {}).get("name")
        for d in docs if d.get("kind") == CLUSTER_ISSUER
    }
    issuers = {
        (doc_namespace(d), (d.get("metadata") or {}).get("name"))
        for d in docs if d.get("kind") == ISSUER
    }
    out: list[str] = []
    seen = 0
    for doc in docs:
        namespace = doc_namespace(doc)
        for kind, name, where in _issuer_refs(doc):
            if kind not in (CLUSTER_ISSUER, ISSUER):
                continue
            seen += 1
            if name in exempt:
                continue
            if kind == CLUSTER_ISSUER:
                if name not in cluster_issuers:
                    out.append(
                        f"  {where}: names {CLUSTER_ISSUER} {name!r}, which the corpus "
                        f"does not ship — the certificate stays Pending and the "
                        f"hostname serves the ingress controller's own certificate "
                        f"(shipped: {sorted(n for n in cluster_issuers if n) or 'none'})"
                    )
            elif (namespace, name) not in issuers:
                out.append(
                    f"  {where}: names {ISSUER} {name!r} in {namespace}, which the "
                    f"corpus does not ship — a namespaced Issuer must live in the "
                    f"referring object's own namespace"
                )
    return out, seen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="cert-manager issuer-reference resolution gate."
    )
    parser.add_argument(
        "--allow-external", action="append", default=[], metavar="NAME=REASON",
        help="issuer name installed outside this corpus, with the reason; repeatable",
    )
    args = parser.parse_args(argv)
    try:
        exempt = parse_exempt(args.allow_external)
    except OperatorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    docs = load_corpus()
    found, seen = violations(docs, exempt)
    if found:
        print(
            "Issuer references that resolve to nothing in this corpus:",
            file=sys.stderr,
        )
        print("\n".join(found), file=sys.stderr)
        return 1

    if not seen:
        print(
            f"ERROR: inspected 0 issuer references in {len(docs)} document(s) — a "
            "gate that checks nothing is not a gate. Check that the `kustomize "
            "build` paths feeding stdin cover the stages declaring Certificates "
            "and TLS-annotated Ingresses.",
            file=sys.stderr,
        )
        return 2

    for name, reason in sorted(exempt.items()):
        print(f"  {name}: installed outside this corpus ({reason})")
    print(
        f"Issuer references OK — {seen} reference(s) across {len(docs)} document(s) "
        "all resolve"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
