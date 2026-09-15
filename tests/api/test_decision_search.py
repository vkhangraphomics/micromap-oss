"""Semantic search over decisions via the Neo4j vector index (#190 pillar 4).

Integration: a real Neo4j (testcontainers) + the deterministic FakeEmbedder
(EMBEDDINGS_PROVIDER=fake). Verifies the vector index path ranks the
semantically-nearest decision first and stays org-scoped, and that search is a
clean 503 when embeddings are unconfigured.
"""
import asyncio
import time

import pytest
from unittest.mock import patch

from api.routes import provenance_decisions as pd


@pytest.fixture(scope="module")
def kg():
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers[neo4j] not installed")
    from integrations.neo4j_microbiome import MicrobiomeKG
    try:
        with Neo4jContainer("neo4j:5.15") as n4j:
            graph = MicrobiomeKG(
                n4j.get_connection_url(), n4j.username, n4j.password, database="neo4j",
            )
            yield graph
            graph.close()
    except Exception as e:  # pragma: no cover - env-dependent
        pytest.skip(f"Could not start Neo4j container: {e}")


def _ingest(kg, event_id, summary, rationale, *, org="acme"):
    event = pd.DecisionEvent(
        id=event_id, tool="nexus", action_type="target_rationale",
        occurred_at="2026-06-11T17:00:00Z", summary=summary, rationale=rationale,
    )
    with patch.object(pd, "get_kg", return_value=kg):
        return asyncio.run(pd.ingest_decision(event, organization_id=org))


def _search(kg, text, *, org="acme", limit=5):
    with patch.object(pd, "get_kg", return_value=kg):
        return asyncio.run(
            pd.search_decisions(pd.DecisionSearchQuery(query=text, limit=limit),
                                organization_id=org)
        )


def test_search_disabled_returns_503(kg, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.delenv("EMBEDDINGS_PROVIDER", raising=False)
    with pytest.raises(HTTPException) as ei:
        _search(kg, "anything")
    assert ei.value.status_code == 503


def test_semantic_search_ranks_nearest_and_scopes_by_org(kg, monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDINGS_DIM", "64")
    pd.ensure_decision_vector_index(kg, 64)

    _ingest(kg, "d-park", "Pursue the butyrate guild for parkinson",
            "the butyrate producing guild is depleted in parkinson patients", org="acme")
    _ingest(kg, "d-budget", "Approve the Q3 budget",
            "quarterly revenue targets and headcount planning", org="acme")
    _ingest(kg, "d-other-org", "Butyrate parkinson rationale",
            "butyrate producing guild parkinson depleted", org="globex")

    # The vector index is eventually consistent — poll until it is populated.
    result = None
    for _ in range(20):
        result = _search(kg, "butyrate guild depleted in parkinson", org="acme")
        if result.count >= 1:
            break
        time.sleep(0.5)

    ids = [d.id for d in result.decisions]
    assert ids, "vector index returned no results after polling"
    assert ids[0] == "d-park"          # semantically nearest, ranked first
    assert "d-other-org" not in ids    # different org is scoped out
    assert result.decisions[0].score > 0
