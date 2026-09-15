"""Synthetic mapping + rows for benchmarks (#79 F5).

A Taxon/Disease/ASSOCIATED_WITH shape (mirrors the Disbiome example). Each row
carries distinct ids so N rows yield N taxa + N diseases + N relationships — no
accidental dedup masking the scale.
"""
from __future__ import annotations

from micromap_mapforge.integration.biocypher.ir import SourceRef
from micromap_mapforge.resolve.pipeline import ResolutionReport


def synth_mapping() -> dict:
    return {
        "source": {"name": "bench", "format": "csv", "path": "/tmp/bench.csv"},
        # match_on fields align with the default schema_config identifiers
        # (Taxon→ncbi_tax_id, Disease→mondo_id) so the resolve benchmark actually
        # resolves against a preloaded graph rather than falling through.
        "entities": [
            {"label": "Taxon", "match_on": "ncbi_tax_id",
             "columns": {"ncbi_tax_id": "tax_id", "name": "tax_name"}},
            {"label": "Disease", "match_on": "mondo_id",
             "columns": {"mondo_id": "disease_id", "name": "disease_name"}},
        ],
        "relationships": [
            {"type": "ASSOCIATED_WITH",
             "from": "Taxon(ncbi_tax_id=row.tax_id)",
             "to": "Disease(mondo_id=row.disease_id)",
             "properties": {"evidence": "evidence_strength"}},
        ],
    }


def synth_rows(n: int) -> list[dict]:
    return [
        {"tax_id": str(1_000_000 + i), "tax_name": f"Taxon {i}",
         "disease_id": f"MONDO:{i:07d}", "disease_name": f"Disease {i}",
         "evidence_strength": "strong"}
        for i in range(n)
    ]


def synth_source_ref() -> SourceRef:
    return SourceRef(kind="file", path="/tmp/bench.csv", sha256="0" * 64)


def fall_through_report() -> ResolutionReport:
    """Empty report → every row falls through. Lets the emit benchmark run fully
    offline (no graph needed) while still exercising the per-row node/edge build."""
    return ResolutionReport()
