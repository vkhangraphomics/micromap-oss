"""Tests for GET /api/v1/provenance/sources.

The endpoint historically counted nodes by `n.sources` (plural list only).
Some loaders (ChEMBL, PubMed/Paper, …) set only the scalar `n.source`, so
~19k production nodes were silently invisible to the endpoint (issue #83).
These tests pin the fixed behavior: the endpoint accepts both shapes.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fake_kg_factory():
    """Return a factory that builds a fake MicrobiomeKG returning canned
    results per (query-pattern, params) call."""
    def _make(node_rows_by_label: dict[str, list[dict]],
              rel_rows: list[dict]) -> MagicMock:
        kg = MagicMock()
        def _exec(query: str, params: dict):
            # node query is per-label; the label is literal in the query
            for label, rows in node_rows_by_label.items():
                if f"MATCH (n:{label})" in query:
                    return rows
            if "MATCH ()-[r]->()" in query:
                return rel_rows
            return []
        kg.execute_cypher = MagicMock(side_effect=_exec)
        return kg
    return _make


@pytest.mark.asyncio
async def test_endpoint_counts_nodes_with_only_scalar_source(fake_kg_factory):
    """Regression for issue #83: a node carrying only `n.source='chembl'` (and
    no `n.sources` list) must still be counted as a chembl-attributed Drug."""
    from api.routes import provenance as prov_mod

    fake = fake_kg_factory(
        node_rows_by_label={
            # Imagine the new tolerant query unions n.sources and n.source.
            # Production rows from labels we don't care about return [].
            "Drug":  [{"name": "chembl", "cnt": 6220}],
            "Paper": [{"name": "pubmed", "cnt": 10000}],
            "Taxon":      [], "Disease": [],   "Metabolite": [],
            "Gene":       [], "Pathway": [],
        },
        rel_rows=[{"name": "chembl", "cnt": 4425}],
    )
    with patch.object(prov_mod, "get_kg", return_value=fake):
        result = await prov_mod.list_data_sources()

    src_by_name = {s["name"]: s for s in result["sources"]}
    assert "chembl" in src_by_name
    assert src_by_name["chembl"]["node_counts"].get("Drug") == 6220
    assert src_by_name["chembl"]["total_nodes"] == 6220
    assert src_by_name["chembl"]["relationship_count"] == 4425
    assert "pubmed" in src_by_name
    assert src_by_name["pubmed"]["node_counts"].get("Paper") == 10000


@pytest.mark.asyncio
async def test_endpoint_query_unions_scalar_and_list_sources(fake_kg_factory):
    """The fix uses COALESCE(n.sources, [n.source]) so the query covers BOTH
    shapes. Pin the query text so a future maintainer can't silently revert
    to list-only counting (which would re-introduce issue #83)."""
    import re
    from api.routes import provenance as prov_mod

    captured: list[str] = []
    kg = MagicMock()
    def _exec(query: str, params: dict):
        captured.append(query)
        return []
    kg.execute_cypher = MagicMock(side_effect=_exec)

    with patch.object(prov_mod, "get_kg", return_value=kg):
        await prov_mod.list_data_sources()

    node_queries = [q for q in captured if "MATCH (n:" in q]
    assert node_queries, "expected per-label node queries"
    # Match `n.source` as a *whole word* — `n.sources` should not satisfy this.
    scalar_ref = re.compile(r"\bn\.source\b(?!s)")
    for q in node_queries:
        assert scalar_ref.search(q), (
            "node query lost the scalar-source fallback — issue #83 regression:\n"
            f"{q}"
        )


@pytest.mark.asyncio
async def test_endpoint_still_counts_nodes_with_only_sources_list(fake_kg_factory):
    """Bugsigdb/Disbiome/gmrepo/gutMDisorder set `n.sources = ['x']`. The
    fix must not regress that path."""
    from api.routes import provenance as prov_mod

    fake = fake_kg_factory(
        node_rows_by_label={
            "Taxon":   [{"name": "disbiome", "cnt": 500}],
            "Disease": [{"name": "disbiome", "cnt": 120}],
            "Metabolite": [], "Drug": [], "Gene": [],
            "Pathway": [], "Paper": [],
        },
        rel_rows=[{"name": "disbiome", "cnt": 4200}],
    )
    with patch.object(prov_mod, "get_kg", return_value=fake):
        result = await prov_mod.list_data_sources()

    src_by_name = {s["name"]: s for s in result["sources"]}
    assert src_by_name["disbiome"]["node_counts"] == {"Taxon": 500, "Disease": 120}
    assert src_by_name["disbiome"]["total_nodes"] == 620
    assert src_by_name["disbiome"]["relationship_count"] == 4200
