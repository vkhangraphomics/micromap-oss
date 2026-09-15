"""Tabular ingestion → Contribution Bundle IR (3b-1, issue #125).

Mirrors the row-by-row decisions made by ``emit/cypher.py`` so the tabular
``inspect → map → resolve → emit`` path can produce a ``ContributionBundle``
and reuse the 3a serializer instead of writing ``mapping.yaml`` / Cypher
directly. Requires the IR discriminator extensions from 3b-0 (#129) so
resolved-vs-fall-through nodes serialize to the correct MATCH-vs-MERGE shape.
"""

from micromap_mapforge.integration.tabular.to_ir import tabular_to_ir

__all__ = ["tabular_to_ir"]
