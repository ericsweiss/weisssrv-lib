#!/usr/bin/env python3
"""Assert every CloudNativePG Cluster is archived, retained, drainable and kept.
Reads the rendered corpus on stdin; exit 0 clean, 1 findings, 2 operator error.
Contract and flags: docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from gate_common import (  # noqa: E402  (resolved from this script's own directory)
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

CNPG_API = "postgresql.cnpg.io/"
PLUGIN_API = "barmancloud.cnpg.io/"
BARMAN_PLUGIN = "barman-cloud.cloudnative-pg.io"
PRUNE_ANNOTATION = "kustomize.toolkit.fluxcd.io/prune"
_WINDOW = re.compile(r"^(\d+)([dwm])$")
_UNIT_DAYS = {"d": 1, "w": 7, "m": 30}


def retention_days(policy: object) -> int | None:
    """Days in a Barman recovery-window policy such as `30d`, else None."""
    match = _WINDOW.match(str(policy).strip()) if policy is not None else None
    return int(match.group(1)) * _UNIT_DAYS[match.group(2)] if match else None


def _of(docs: list[dict], api: str, kind: str) -> list[dict]:
    return [
        d for d in docs
        if str(d.get("apiVersion", "")).startswith(api) and d.get("kind") == kind
    ]


def _plugin_store(spec: dict) -> str | None:
    """The ObjectStore a WAL-archiving Barman plugin entry names, else None."""
    for plugin in spec.get("plugins") or []:
        if (isinstance(plugin, dict) and plugin.get("name") == BARMAN_PLUGIN
                and plugin.get("isWALArchiver")):
            return str((plugin.get("parameters") or {}).get("barmanObjectName"))
    return None


def violations(docs: list[dict], min_days: int) -> tuple[list[str], int]:
    """-> (violations, clusters inspected)."""
    clusters = _of(docs, CNPG_API, "Cluster")
    stores = {
        (doc_namespace(d), (d.get("metadata") or {}).get("name")): d
        for d in _of(docs, PLUGIN_API, "ObjectStore")
    }
    scheduled = {
        (doc_namespace(d), ((d.get("spec") or {}).get("cluster") or {}).get("name"))
        for d in _of(docs, CNPG_API, "ScheduledBackup")
    }
    out: list[str] = []
    for cluster in clusters:
        where = doc_key(cluster)
        meta = cluster.get("metadata") or {}
        spec = cluster.get("spec") or {}
        namespace = doc_namespace(cluster)
        store_name = _plugin_store(spec)
        in_tree = (spec.get("backup") or {}).get("barmanObjectStore")
        if store_name is not None:
            store = stores.get((namespace, store_name))
            if store is None:
                out.append(f"  {where}: names ObjectStore {store_name!r}, which the corpus does not ship")
            else:
                days = retention_days((store.get("spec") or {}).get("retentionPolicy"))
                if days is None or days < min_days:
                    out.append(f"  {where}: ObjectStore {store_name} keeps {days} day(s), under the {min_days}-day floor")
        elif in_tree:
            days = retention_days((spec.get("backup") or {}).get("retentionPolicy"))
            if days is None or days < min_days:
                out.append(f"  {where}: backup.retentionPolicy keeps {days} day(s), under the {min_days}-day floor")
        else:
            out.append(f"  {where}: no WAL archiver, so point-in-time recovery is impossible")
        if (namespace, meta.get("name")) not in scheduled:
            out.append(f"  {where}: no ScheduledBackup targets this cluster")
        if int(spec.get("instances", 1)) == 1 and spec.get("enablePDB", True) is not False:
            out.append(f"  {where}: one instance with a PodDisruptionBudget blocks every node drain; set enablePDB: false")
        if (meta.get("annotations") or {}).get(PRUNE_ANNOTATION) != "disabled":
            out.append(f"  {where}: no {PRUNE_ANNOTATION}: disabled, so removing the file deletes the database")
    return out, len(clusters)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--min-retention-days", type=int, default=30)
    args = parser.parse_args(argv)
    docs = load_corpus()
    found, seen = violations(docs, args.min_retention_days)
    if found:
        print("CloudNativePG clusters missing a backup, drain or prune safeguard:", file=sys.stderr)
        print("\n".join(found), file=sys.stderr)
        return 1
    if not seen:
        print(
            f"ERROR: inspected 0 CloudNativePG Clusters in {len(docs)} document(s); "
            "a gate that checks nothing is not a gate. Pipe the build that declares them.",
            file=sys.stderr,
        )
        return 2
    print(f"CloudNativePG policy OK: {seen} cluster(s) archived, retained, drainable and kept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
