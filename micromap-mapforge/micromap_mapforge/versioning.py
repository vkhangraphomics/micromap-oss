"""KG version diff (C8, #76): the incremental delta between two bundle versions.

Reactome v89 -> v90, Bgee quarterly drops: a customer should ingest only what
changed, not re-load millions of edges. ``serialize_bundle`` writes a per-bundle
IR snapshot (``bundle.ir.json``); ``diff_bundles`` compares two snapshots into
added / removed / changed nodes and edges.

Identity: a node is keyed by ``(label, id)``, an edge by ``(type, from_id,
to_id)``. "Changed" = same key, different ``properties``. (Applying the delta as
MERGE/DELETE Cypher is the next increment; this first pass computes + reports it.)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SNAPSHOT_FILENAME = "bundle.ir.json"


def _node_key(n: dict) -> tuple[Any, Any]:
    return (n.get("label"), n.get("id"))


def _edge_key(e: dict) -> tuple[Any, Any, Any]:
    return (e.get("type"), e.get("from_id"), e.get("to_id"))


def _props(x: dict) -> dict:
    return x.get("properties") or {}


@dataclass
class BundleDiff:
    nodes_added: list[dict]
    nodes_removed: list[dict]
    nodes_changed: list[dict]   # {label, id, before, after}
    edges_added: list[dict]
    edges_removed: list[dict]
    edges_changed: list[dict]   # {type, from_id, to_id, before, after}

    def summary(self) -> dict[str, int]:
        return {
            "nodes_added": len(self.nodes_added),
            "nodes_removed": len(self.nodes_removed),
            "nodes_changed": len(self.nodes_changed),
            "edges_added": len(self.edges_added),
            "edges_removed": len(self.edges_removed),
            "edges_changed": len(self.edges_changed),
        }

    def is_empty(self) -> bool:
        return not any(self.summary().values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "nodes_added": self.nodes_added,
            "nodes_removed": self.nodes_removed,
            "nodes_changed": self.nodes_changed,
            "edges_added": self.edges_added,
            "edges_removed": self.edges_removed,
            "edges_changed": self.edges_changed,
        }


def diff_bundles(old: dict, new: dict) -> BundleDiff:
    """Compute the delta from ``old`` to ``new`` (both IR schema-dicts)."""
    on = {_node_key(n): n for n in old.get("nodes", [])}
    nn = {_node_key(n): n for n in new.get("nodes", [])}
    nodes_added = [nn[k] for k in sorted(nn.keys() - on.keys())]
    nodes_removed = [on[k] for k in sorted(on.keys() - nn.keys())]
    nodes_changed = [
        {"label": k[0], "id": k[1], "before": _props(on[k]), "after": _props(nn[k])}
        for k in sorted(on.keys() & nn.keys())
        if _props(on[k]) != _props(nn[k])
    ]

    oe = {_edge_key(e): e for e in old.get("edges", [])}
    ne = {_edge_key(e): e for e in new.get("edges", [])}
    edges_added = [ne[k] for k in sorted(ne.keys() - oe.keys())]
    edges_removed = [oe[k] for k in sorted(oe.keys() - ne.keys())]
    edges_changed = [
        {"type": k[0], "from_id": k[1], "to_id": k[2],
         "before": _props(oe[k]), "after": _props(ne[k])}
        for k in sorted(oe.keys() & ne.keys())
        if _props(oe[k]) != _props(ne[k])
    ]

    return BundleDiff(
        nodes_added, nodes_removed, nodes_changed,
        edges_added, edges_removed, edges_changed,
    )


def load_ir_snapshot(bundle_or_snapshot: str | Path) -> dict:
    """Load a bundle's IR snapshot. Accepts a bundle directory (reads its
    ``bundle.ir.json``) or a snapshot file directly."""
    p = Path(bundle_or_snapshot)
    snap = p / SNAPSHOT_FILENAME if p.is_dir() else p
    if not snap.is_file():
        raise FileNotFoundError(
            f"{snap}: no IR snapshot. Emitting/importing a bundle writes "
            f"{SNAPSHOT_FILENAME}; re-run emit/import-kg to produce one."
        )
    return json.loads(snap.read_text(encoding="utf-8"))


def diff_bundle_dirs(old_dir: str | Path, new_dir: str | Path) -> BundleDiff:
    return diff_bundles(load_ir_snapshot(old_dir), load_ir_snapshot(new_dir))
