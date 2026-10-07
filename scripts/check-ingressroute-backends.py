#!/usr/bin/env python3
"""Assert route backends and scrape ports resolve to a Service in the corpus.

A renamed Service or port leaves `kustomize build` and kubeconform green, and
surfaces as a Traefik 503 or an unscraped target. Contract: docs/SCRIPTS.md.
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

ROUTE_KINDS = ("IngressRoute", "IngressRouteTCP", "IngressRouteUDP")
# A TraefikService is Traefik's own weighted/mirrored object, not a Service.
BACKEND_KINDS = ("", "Service")


class Services:
    """The Services the corpus declares, indexed for both kinds of lookup."""

    def __init__(self, docs: list[dict]):
        self.by_key: dict[tuple[str, str], dict] = {}
        self.namespaces: set[str] = set()
        for doc in docs:
            if doc.get("kind") != "Service":
                continue
            namespace = doc_namespace(doc)
            name = (doc.get("metadata") or {}).get("name")
            if not isinstance(name, str):
                continue
            self.namespaces.add(namespace)
            self.by_key[(namespace, name)] = doc

    def ports(self, namespace: str, name: str) -> tuple[set, set]:
        """-> (port numbers, port names) one Service declares."""
        service = self.by_key.get((namespace, name))
        numbers: set = set()
        names: set = set()
        for port in ((service or {}).get("spec") or {}).get("ports") or []:
            if not isinstance(port, dict):
                continue
            if isinstance(port.get("port"), int):
                numbers.add(port["port"])
            if isinstance(port.get("name"), str):
                names.add(port["name"])
        return numbers, names

    def matching(self, namespace: str, labels: dict) -> list[tuple[str, dict]]:
        """-> [(name, Service)] in `namespace` whose labels cover `labels`."""
        out = []
        for (ns, name), doc in sorted(self.by_key.items()):
            if ns != namespace:
                continue
            have = (doc.get("metadata") or {}).get("labels") or {}
            if not isinstance(have, dict):
                continue
            if all(have.get(k) == v for k, v in labels.items()):
                out.append((name, doc))
        return out


def parse_allowed(values: list[str]) -> dict[tuple[str, str], str]:
    """`NAMESPACE/NAME=REASON` pairs for a Service rendered outside this corpus."""
    allowed: dict[tuple[str, str], str] = {}
    for raw in values or []:
        ref, sep, reason = raw.partition("=")
        namespace, slash, name = ref.partition("/")
        if not (sep and slash and namespace.strip() and name.strip() and reason.strip()):
            raise OperatorError(
                f"--allow-backend takes NAMESPACE/NAME=REASON, got {raw!r}"
            )
        allowed[(namespace.strip(), name.strip())] = reason.strip()
    return allowed


def _route_backends(doc: dict) -> list[dict]:
    """Every `spec.routes[].services[]` entry, flattened."""
    out = []
    for route in ((doc.get("spec") or {}).get("routes") or []):
        if not isinstance(route, dict):
            continue
        for entry in route.get("services") or []:
            if isinstance(entry, dict):
                out.append(entry)
    return out


def backend_violations(
    docs: list[dict], services: Services, allowed: dict[tuple[str, str], str]
) -> tuple[list[str], int]:
    """-> (violations, backends resolved). A backend outside the corpus is skipped."""
    out: list[str] = []
    seen = 0
    for doc in docs:
        if doc.get("kind") not in ROUTE_KINDS:
            continue
        label = doc_key(doc)
        own_namespace = doc_namespace(doc)
        for entry in _route_backends(doc):
            if str(entry.get("kind") or "") not in BACKEND_KINDS:
                continue
            name = entry.get("name")
            if not isinstance(name, str):
                out.append("  %s: a backend entry declares no name" % label)
                continue
            namespace = entry.get("namespace") or own_namespace
            # A namespace this corpus renders no Service for is out of scope:
            # the Service belongs to another repo or another render path.
            if namespace not in services.namespaces:
                continue
            seen += 1
            if (namespace, name) not in services.by_key:
                if (namespace, name) in allowed:
                    continue
                out.append(
                    "  %s: backend %s/%s names no Service in the corpus"
                    % (label, namespace, name)
                )
                continue
            port = entry.get("port")
            if port is None:
                continue
            numbers, names = services.ports(namespace, name)
            if port in numbers or port in names:
                continue
            declared = sorted(
                [str(p) for p in numbers] + ["%r" % n for n in names]
            )
            out.append(
                "  %s: backend %s/%s port %r is neither a port nor a port name "
                "of that Service (it declares %s)"
                % (label, namespace, name, port, ", ".join(declared) or "no port")
            )
    return out, seen


def _monitor_namespaces(doc: dict, own: str) -> list[str] | None:
    """The namespaces a ServiceMonitor selects, or None for every namespace."""
    selector = (doc.get("spec") or {}).get("namespaceSelector") or {}
    if not isinstance(selector, dict):
        return [own]
    if selector.get("any") is True:
        return None
    names = selector.get("matchNames")
    if isinstance(names, list) and names:
        return [str(n) for n in names]
    return [own]


def monitor_violations(docs: list[dict], services: Services) -> tuple[list[str], int]:
    """-> (violations, endpoints resolved). A `port` here is a port NAME."""
    out: list[str] = []
    seen = 0
    for doc in docs:
        if doc.get("kind") != "ServiceMonitor":
            continue
        label = doc_key(doc)
        spec = doc.get("spec") or {}
        selector = spec.get("selector") or {}
        labels = selector.get("matchLabels") if isinstance(selector, dict) else None
        if not isinstance(labels, dict) or not labels:
            # matchExpressions alone, or no selector: the gate cannot resolve the
            # Service set without the API, so it does not guess.
            continue
        namespaces = _monitor_namespaces(doc, doc_namespace(doc))
        search = sorted(services.namespaces) if namespaces is None else namespaces
        candidates = [
            (ns, name) for ns in search for name, _ in services.matching(ns, labels)
        ]
        if not candidates:
            continue
        for endpoint in spec.get("endpoints") or []:
            if not isinstance(endpoint, dict):
                continue
            port = endpoint.get("port")
            if not isinstance(port, str):
                # targetPort addresses the pod directly, bypassing the name.
                continue
            seen += 1
            declared = {
                name
                for ns, service in candidates
                for name in services.ports(ns, service)[1]
            }
            if port in declared:
                continue
            out.append(
                "  %s: endpoint port %r is no port NAME on the Service(s) it "
                "selects (%s declare %s)"
                % (
                    label, port,
                    ", ".join("%s/%s" % c for c in candidates),
                    ", ".join(sorted(declared)) or "no named port",
                )
            )
    return out, seen


def violations(
    docs: list[dict], allowed: dict[tuple[str, str], str] | None = None
) -> tuple[list[str], int]:
    """-> (violations, references resolved) across routes and ServiceMonitors."""
    services = Services(docs)
    backend, backend_seen = backend_violations(docs, services, allowed or {})
    monitor, monitor_seen = monitor_violations(docs, services)
    return backend + monitor, backend_seen + monitor_seen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="route-backend and scrape-port resolution gate."
    )
    parser.add_argument(
        "--allow-backend", action="append", default=[], metavar="NAMESPACE/NAME=REASON",
        help="backend Service rendered outside this corpus, with the reason; repeatable",
    )
    args = parser.parse_args(argv)
    try:
        allowed = parse_allowed(args.allow_backend)
    except OperatorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    docs = load_corpus()
    found, seen = violations(docs, allowed)
    if found:
        print(
            "Route backends or scrape ports that resolve to nothing. Both render "
            "and schema-validate cleanly; the cluster answers with a 503 or an "
            "unscraped target:",
            file=sys.stderr,
        )
        print("\n".join(found), file=sys.stderr)
        return 1

    if not seen:
        print(
            "ERROR: resolved 0 references in %d document(s) — a gate that checks "
            "nothing is not a gate. Check that the `kustomize build` paths "
            "feeding stdin render the IngressRoutes, ServiceMonitors AND the "
            "Services they name." % len(docs),
            file=sys.stderr,
        )
        return 2

    for (namespace, name), reason in sorted(allowed.items()):
        print("  %s/%s: rendered outside this corpus (%s)" % (namespace, name, reason))
    print(
        "route and scrape references OK — %d resolved across %d document(s)"
        % (seen, len(docs))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
