"""Submit benchmark (#79 F5): write throughput of MicroMapCoreExecutor.

Emits a bundle from N synthetic rows (N Taxon + N Disease + N rels), then runs
the executor against Neo4j and reports nodes+rels written per second.

    python -m micromap_mapforge.benchmarks.submit --rows 10000
    NEO4J_URI=bolt://host:7687 NEO4J_PASSWORD=... \
      python -m micromap_mapforge.benchmarks.submit --rows 100000
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from micromap_mapforge.emit.bundle import IngestBundle
from micromap_mapforge.integration.biocypher.serialize import serialize_bundle
from micromap_mapforge.integration.tabular import tabular_to_ir
from micromap_mapforge.submit.core import MicroMapCoreExecutor

from ._neo4j import provision_driver
from .measure import measure
from .synth import fall_through_report, synth_mapping, synth_rows, synth_source_ref


def bench_submit(driver, *, n_rows: int, database: str = "neo4j") -> dict:
    """Emit a bundle from ``n_rows`` rows and time the executor write."""
    bundle = tabular_to_ir(
        mapping=synth_mapping(), rows=synth_rows(n_rows), report=fall_through_report(),
        organization_id="bench-org", source_ref=synth_source_ref(),
    )
    with tempfile.TemporaryDirectory(prefix="bench-submit-") as d:
        serialize_bundle(bundle, out_dir=Path(d), include_provenance=True)
        ingest = IngestBundle(root=Path(d))
        receipt, m = measure(
            lambda: MicroMapCoreExecutor(driver=driver, database=database).submit(ingest)
        )

    written = receipt.nodes_written + receipt.relationships_written
    return {
        "metric": "submit",
        "n_rows": n_rows,
        "nodes_written": receipt.nodes_written,
        "relationships_written": receipt.relationships_written,
        "wall_s": round(m.wall_s, 3),
        "writes_per_s": round(written / m.wall_s) if m.wall_s else None,
        "peak_rss_mb": round(m.peak_rss_mb, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Submit benchmark (#79 F5)")
    ap.add_argument("--rows", type=int, default=10_000, help="source rows")
    args = ap.parse_args()
    with provision_driver() as driver:
        print(json.dumps(bench_submit(driver, n_rows=args.rows), indent=2))


if __name__ == "__main__":
    main()
