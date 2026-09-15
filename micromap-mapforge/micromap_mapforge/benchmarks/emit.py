"""Emit benchmark (#79 F5): tabular_to_ir + serialize over N rows.

Offline — no Neo4j. Exercises the per-row IR build (``tabular_to_ir``) plus the
on-disk serialization (``serialize_bundle``), which is the bulk of ``mapforge
emit``. Headline F5 target: max wall time for emit at 100K source rows.

    python -m micromap_mapforge.benchmarks.emit --rows 100000
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from micromap_mapforge.integration.biocypher.serialize import serialize_bundle
from micromap_mapforge.integration.tabular import tabular_to_ir

from .measure import measure
from .synth import fall_through_report, synth_mapping, synth_rows, synth_source_ref


def bench_emit(n_rows: int) -> dict:
    """Return a metrics dict for emitting a bundle from ``n_rows`` synthetic rows."""
    mapping = synth_mapping()
    rows = synth_rows(n_rows)
    report = fall_through_report()
    src = synth_source_ref()

    def _run():
        bundle = tabular_to_ir(
            mapping=mapping, rows=rows, report=report,
            organization_id="bench-org", source_ref=src,
        )
        with tempfile.TemporaryDirectory(prefix="bench-emit-") as d:
            serialize_bundle(bundle, out_dir=Path(d), include_provenance=True)
        return bundle

    bundle, m = measure(_run)
    return {
        "metric": "emit",
        "n_rows": n_rows,
        "nodes": len(bundle.nodes),
        "edges": len(bundle.edges),
        "wall_s": round(m.wall_s, 3),
        "rows_per_s": round(n_rows / m.wall_s) if m.wall_s else None,
        "peak_rss_mb": round(m.peak_rss_mb, 1),
        "rss_growth_mb": round(m.rss_growth_mb, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Emit benchmark (#79 F5)")
    ap.add_argument("--rows", type=int, default=100_000, help="source rows (default 100K)")
    print(json.dumps(bench_emit(ap.parse_args().rows), indent=2))


if __name__ == "__main__":
    main()
