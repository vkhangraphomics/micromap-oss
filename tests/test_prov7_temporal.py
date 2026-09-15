"""
Test PROV-7: SUPERSEDED_BY edge creation on ingest + current-truth /
as-of queries.

Follows the PROV-2 test style: monkeypatches get_kg so no live Neo4j is
required.
"""

from unittest.mock import MagicMock, patch

import pytest

from api.routes.provenance_decisions import DecisionEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_kg(*side_effects):
    """Return a mock KG whose execute_cypher returns each effect in sequence."""
    kg = MagicMock()
    kg.execute_cypher = MagicMock(side_effect=list(side_effects))
    return kg


def _decision_row(**overrides):
    base = {
        "id": "dec-B",
        "action_type": "hypothesis_verdict",
        "summary": "Updated crohn frame",
        "rationale": "New cohort data",
        "tool": "nexus",
        "occurred_at": "2026-06-09T00:00:00Z",
        "actor_user": "u1",
        "actor_org": "acme",
        "role": "scientist",
        "about": ["Crohn disease"],
        "citations": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Model-level tests (no HTTP, no KG)
# ---------------------------------------------------------------------------

def test_supersedes_field_accepted_flat():
    """DecisionEvent accepts supersedes as a flat list of strings."""
    event = DecisionEvent(
        id="dec-B",
        tool="nexus",
        action_type="hypothesis_verdict",
        occurred_at="2026-06-09T00:00:00Z",
        summary="s",
        supersedes=["dec-A"],
    )
    assert event.supersedes == ["dec-A"]


def test_supersedes_populated_from_source_ref_parents():
    """source_ref.parents is folded into supersedes by the model_validator."""
    event = DecisionEvent(
        id="dec-B",
        tool="nexus",
        action_type="hypothesis_verdict",
        occurred_at="2026-06-09T00:00:00Z",
        summary="s",
        source_ref={"tool": "nexus", "parents": ["dec-A", "dec-X"]},
    )
    assert "dec-A" in event.supersedes
    assert "dec-X" in event.supersedes


def test_supersedes_deduped_when_explicit_and_parents_overlap():
    """If the same id appears in both supersedes and source_ref.parents it
    should appear only once in the final list."""
    event = DecisionEvent(
        id="dec-B",
        tool="nexus",
        action_type="hypothesis_verdict",
        occurred_at="2026-06-09T00:00:00Z",
        summary="s",
        supersedes=["dec-A"],
        source_ref={"tool": "nexus", "parents": ["dec-A", "dec-X"]},
    )
    assert event.supersedes.count("dec-A") == 1
    assert "dec-X" in event.supersedes


def test_supersedes_defaults_to_empty():
    """DecisionEvent without supersedes field defaults to []."""
    event = DecisionEvent(
        id="dec-B",
        tool="nexus",
        action_type="hypothesis_verdict",
        occurred_at="2026-06-09T00:00:00Z",
        summary="s",
    )
    assert event.supersedes == []


def test_supersedes_not_stored_as_node_property():
    """supersedes is excluded from the params passed to the MERGE Cypher —
    it is not a node property, it becomes an edge."""
    event = DecisionEvent(
        id="dec-B",
        tool="nexus",
        action_type="hypothesis_verdict",
        occurred_at="2026-06-09T00:00:00Z",
        summary="s",
        supersedes=["dec-A"],
    )
    # model_dump excluding entity_tags and supersedes (as the route does)
    params = event.model_dump(exclude={"entity_tags", "supersedes"})
    assert "supersedes" not in params


# ---------------------------------------------------------------------------
# Ingest: SUPERSEDED_BY edges via the async route
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_with_supersedes_creates_edge_and_reports_count():
    """Ingest decision B with supersedes=['A'] → supersedes_linked == 1."""
    from api.routes import provenance_decisions as mod

    # Note: entity_tags is empty so the ABOUT query block is skipped.
    # execute_cypher is called twice: MERGE node, then SUPERSEDED_BY.
    kg = _make_kg(
        None,   # MERGE decision node
        # SUPERSEDED_BY query — one row: prior 'dec-A' found and linked
        [{"prior_id": "dec-A", "linked": True}],
    )
    event = DecisionEvent(
        id="dec-B",
        tool="nexus",
        action_type="hypothesis_verdict",
        occurred_at="2026-06-09T00:00:00Z",
        summary="Updated Crohn hypothesis",
        supersedes=["dec-A"],
    )
    with patch.object(mod, "get_kg", return_value=kg):
        result = await mod.ingest_decision(event, organization_id="acme")

    assert result.supersedes_linked == 1
    assert result.id == "dec-B"

    # Verify the SUPERSEDED_BY Cypher was issued
    calls = [call[0][0] for call in kg.execute_cypher.call_args_list]
    assert any("SUPERSEDED_BY" in q for q in calls), (
        "Expected a SUPERSEDED_BY Cypher statement to be issued"
    )
    # Verify the correct params were passed for the supersession query
    sup_params = next(
        call[0][1]
        for call in kg.execute_cypher.call_args_list
        if "SUPERSEDED_BY" in call[0][0]
    )
    assert sup_params["id"] == "dec-B"
    assert sup_params["supersedes"] == ["dec-A"]


@pytest.mark.asyncio
async def test_ingest_supersedes_unknown_id_is_noop():
    """supersedes referencing an id not in the graph → no error, linked==0."""
    from api.routes import provenance_decisions as mod

    kg = _make_kg(
        None,   # MERGE decision node
        [],     # ABOUT query
        # SUPERSEDED_BY query — prior not found
        [{"prior_id": "unknown-dec", "linked": False}],
    )
    event = DecisionEvent(
        id="dec-C",
        tool="nexus",
        action_type="hypothesis_verdict",
        occurred_at="2026-06-09T00:00:00Z",
        summary="New frame",
        supersedes=["unknown-dec"],
    )
    with patch.object(mod, "get_kg", return_value=kg):
        result = await mod.ingest_decision(event, organization_id="acme")

    assert result.supersedes_linked == 0


@pytest.mark.asyncio
async def test_ingest_no_supersedes_skips_cypher_call():
    """When supersedes is empty, no SUPERSEDED_BY Cypher is issued."""
    from api.routes import provenance_decisions as mod

    kg = _make_kg(
        None,   # MERGE decision node
        [],     # ABOUT query
    )
    event = DecisionEvent(
        id="dec-D",
        tool="nexus",
        action_type="hypothesis_verdict",
        occurred_at="2026-06-09T00:00:00Z",
        summary="Baseline decision",
    )
    with patch.object(mod, "get_kg", return_value=kg):
        result = await mod.ingest_decision(event, organization_id="acme")

    assert result.supersedes_linked == 0
    calls = [call[0][0] for call in kg.execute_cypher.call_args_list]
    assert not any("SUPERSEDED_BY" in q for q in calls)


@pytest.mark.asyncio
async def test_ingest_supersedes_result_has_supersedes_linked_field():
    """DecisionIngestResult always has supersedes_linked (defaults to 0)."""
    from api.routes import provenance_decisions as mod

    kg = _make_kg(None, [])
    event = DecisionEvent(
        id="dec-E",
        tool="nexus",
        action_type="hypothesis_verdict",
        occurred_at="2026-06-09T00:00:00Z",
        summary="s",
    )
    with patch.object(mod, "get_kg", return_value=kg):
        result = await mod.ingest_decision(event, organization_id="demo")

    assert hasattr(result, "supersedes_linked")
    assert result.supersedes_linked == 0


# ---------------------------------------------------------------------------
# /provenance/current — non-superseded decisions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_current_returns_only_non_superseded():
    """/provenance/current returns B but NOT A after A is superseded by B."""
    from api.routes import provenance_decisions as mod

    # Only B is returned (A has been superseded)
    kg = _make_kg([_decision_row(id="dec-B")])
    with patch.object(mod, "get_kg", return_value=kg):
        result = await mod.query_current_decisions(
            mod.DecisionQuery(entity="crohn"),
            organization_id="acme",
        )

    assert result.count == 1
    assert result.decisions[0].id == "dec-B"

    # Verify the Cypher filters out superseded nodes
    cypher = kg.execute_cypher.call_args[0][0]
    assert "SUPERSEDED_BY" in cypher
    assert "NOT" in cypher


@pytest.mark.asyncio
async def test_current_returns_empty_when_all_superseded():
    """/provenance/current returns nothing if all decisions are superseded."""
    from api.routes import provenance_decisions as mod

    kg = _make_kg([])
    with patch.object(mod, "get_kg", return_value=kg):
        result = await mod.query_current_decisions(
            mod.DecisionQuery(entity="crohn"),
            organization_id="acme",
        )

    assert result.count == 0
    assert result.decisions == []


@pytest.mark.asyncio
async def test_current_passes_org_scope():
    """/provenance/current passes organization_id to the KG query."""
    from api.routes import provenance_decisions as mod

    kg = _make_kg([])
    with patch.object(mod, "get_kg", return_value=kg):
        await mod.query_current_decisions(
            mod.DecisionQuery(entity="crohn"),
            organization_id="my-org",
        )

    _, params = kg.execute_cypher.call_args[0]
    assert params["organization_id"] == "my-org"


# ---------------------------------------------------------------------------
# /provenance/as-of — valid-time query
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_as_of_before_supersession_returns_old_decision():
    """as-of a date before B was ingested should return A (the then-current
    decision), not B."""
    from api.routes import provenance_decisions as mod

    old_row = _decision_row(
        id="dec-A",
        occurred_at="2026-06-01T00:00:00Z",
        summary="Original Crohn hypothesis",
    )
    kg = _make_kg([old_row])
    with patch.object(mod, "get_kg", return_value=kg):
        result = await mod.query_decisions_as_of(
            mod.AsOfQuery(entity="crohn", as_of="2026-06-05"),
            organization_id="acme",
        )

    assert result.count == 1
    assert result.decisions[0].id == "dec-A"

    # Verify the Cypher includes the as_of constraint
    cypher = kg.execute_cypher.call_args[0][0]
    assert "$as_of" in cypher


@pytest.mark.asyncio
async def test_as_of_passes_correct_params():
    """as-of passes entity, as_of, organization_id, and limit to the KG."""
    from api.routes import provenance_decisions as mod

    kg = _make_kg([])
    with patch.object(mod, "get_kg", return_value=kg):
        await mod.query_decisions_as_of(
            mod.AsOfQuery(entity="parkinson", as_of="2026-06-08", limit=10),
            organization_id="tenant-x",
        )

    _, params = kg.execute_cypher.call_args[0]
    assert params["entity"] == "parkinson"
    # A bare date is normalized to an inclusive end-of-day bound so same-day
    # decisions are not silently excluded by the lexicographic compare.
    assert params["as_of"] == "2026-06-08T23:59:59Z"
    assert params["organization_id"] == "tenant-x"
    # Over-fetch by one to detect truncation (has_more) — keyset pagination
    # (#323). With no cursor the keyset params are null (pass-everything).
    assert params["limit_plus1"] == 11
    assert params["cursor_ts"] is None and params["cursor_id"] is None


def test_as_of_upper_bound_normalizes_bare_date_only():
    """A bare YYYY-MM-DD becomes an inclusive end-of-day bound; a full RFC3339
    timestamp passes through unchanged. This is the PROV-7 as-of off-by-one fix:
    without it, `occurred_at <= '2026-06-08'` excludes `2026-06-08T17:00:00Z`."""
    from api.routes.provenance_decisions import _as_of_upper_bound

    assert _as_of_upper_bound("2026-06-08") == "2026-06-08T23:59:59Z"
    assert _as_of_upper_bound("2026-06-08T17:00:00Z") == "2026-06-08T17:00:00Z"
    assert _as_of_upper_bound("2026-06-08T00:00:00+00:00") == "2026-06-08T00:00:00+00:00"


@pytest.mark.asyncio
async def test_as_of_bare_date_includes_same_day_decision():
    """Regression: a same-day decision (timestamp on the as-of date) must be
    returned for a bare-date as_of — the param reaching Cypher is end-of-day,
    so `'2026-06-08T17:00:00Z' <= '2026-06-08T23:59:59Z'` is True."""
    from api.routes import provenance_decisions as mod

    same_day = _decision_row(id="dec-today", occurred_at="2026-06-08T17:00:00Z")
    kg = _make_kg([same_day])
    with patch.object(mod, "get_kg", return_value=kg):
        result = await mod.query_decisions_as_of(
            mod.AsOfQuery(entity="crohn", as_of="2026-06-08"),
            organization_id="acme",
        )

    assert result.count == 1 and result.decisions[0].id == "dec-today"
    _, params = kg.execute_cypher.call_args[0]
    assert params["as_of"] == "2026-06-08T23:59:59Z"


@pytest.mark.asyncio
async def test_as_of_uses_not_exists_subquery():
    """as-of Cypher excludes decisions superseded within the as-of window."""
    from api.routes import provenance_decisions as mod

    kg = _make_kg([])
    with patch.object(mod, "get_kg", return_value=kg):
        await mod.query_decisions_as_of(
            mod.AsOfQuery(entity="crohn", as_of="2026-06-08"),
            organization_id="acme",
        )

    cypher = kg.execute_cypher.call_args[0][0]
    assert "NOT EXISTS" in cypher
    assert "SUPERSEDED_BY" in cypher
    assert "$as_of" in cypher
