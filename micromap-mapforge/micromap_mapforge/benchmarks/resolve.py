"""Resolve benchmark (#79 F5): max RSS while resolving against an N-node graph.

``build_resolvers`` preloads each label's identifier index into memory, so peak
RSS is dominated by graph size — the headline F5 target is **1M nodes**. We
measure peak RSS across the preload + resolve.

    # throwaway container, small smoke scale:
    python -m micromap_mapforge.benchmarks.resolve --nodes 10000 --rows 5000
    # the real target against a provisioned instance:
    NEO4J_URI=bolt://host:7687 NEO4J_PASSWORD=... \
      python -m micromap_mapforge.benchmarks.resolve --nodes 1000000 --rows 10000
"""
from __future__ import annotations

import argparse
import json

from micromap_mapforge.resolve.pipeline import resolve_mapping
from micromap_mapforge.resolve.registry import build_resolvers

from ._neo4j import preload_taxa_diseases, provision_driver
from .measure import measure
from .synth import synth_mapping, synth_rows


def bench_resolve(driver, *, n_nodes: int, n_rows: int, database: str = "neo4j") -> dict:
    """Preload ``n_nodes`` Taxon + ``n_nodes`` Disease, then resolve ``n_rows``."""
    preload_taxa_diseases(driver, n_nodes, database=database)
    mapping = synth_mapping()
    rows = synth_rows(n_rows)

    def _run():
        resolvers = build_resolvers(
            driver, database=database, labels_of_interest={"Taxon", "Disease"},
        )
        return resolve_mapping(mapping, rows, resolvers)

    report, m = measure(_run)
    return {
        "metric": "resolve",
        "graph_nodes": n_nodes * 2,
        "rows_resolved": n_rows,
        "resolved_count": report.resolved_count,
        "wall_s": round(m.wall_s, 3),
        "peak_rss_mb": round(m.peak_rss_mb, 1),
        "rss_growth_mb": round(m.rss_growth_mb, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Resolve benchmark (#79 F5)")
    ap.add_argument("--nodes", type=int, default=10_000, help="Taxon (and Disease) count")
    ap.add_argument("--rows", type=int, default=5_000, help="rows to resolve")
    args = ap.parse_args()
    with provision_driver() as driver:
        print(json.dumps(bench_resolve(driver, n_nodes=args.nodes, n_rows=args.rows), indent=2))


if __name__ == "__main__":
    main()
