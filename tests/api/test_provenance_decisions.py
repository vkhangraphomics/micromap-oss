"""Tests for the Decision Provenance routes (#190): POST /api/v1/provenance/
{decisions,query,ledger}, and the org-from-API-key binding that makes
org-scoping a real boundary (a client cannot write/read another org by setting
organization_id in the body — it isn't a request field).
"""

from unittest.mock import patch

import pytest


class _FakeKG:
    """Records (query, params) calls; returns canned rows by query shape."""

    def __init__(self, about_rows=None, query_rows=None, ledger_rows=None, recorded_rows=None):
        self.calls = []
        self._about = about_rows or []
        self._query = query_rows or []
        self._ledger = ledger_rows or []
        self._recorded = recorded_rows or []

    def execute_cypher(self, query, params):
        self.calls.append((query, params))
        if "tp.raw AS tag" in query:               # ingest: ABOUT-match step
            return self._about
        if "an IS NOT NULL AS linked" in query:    # ingest: RECORDED-link step (#258)
            return self._recorded
        if "RETURN d.id AS id" in query:           # query route
            return self._query
        if "CALL {" in query:                       # ledger route
            return self._ledger
        return []                                   # ingest MERGE step


def _decision_row(**over):
    row = {
        "id": "dec-1", "action_type": "target_rationale", "summary": "s",
        "rationale": "r", "tool": "nexus", "occurred_at": "2026-06-08T00:00:00Z",
        "actor_user": "v", "actor_org": "g", "role": "ceo",
        "about": ["Parkinson disease"], "citations": ["zenodo:1"],
    }
    row.update(over)
    return row


# --- org resolution from the API key --------------------------------------

@pytest.mark.asyncio
async def test_resolve_org_from_key_mapping_and_default():
    from api.dependencies import resolve_organization_id
    with patch.dict("os.environ", {"API_KEY_ORGS": "k1:acme, k2:graphomics"}, clear=False):
        assert await resolve_organization_id(x_api_key="k1") == "acme"
        assert await resolve_organization_id(x_api_key="k2") == "graphomics"
        # unmapped key -> DEFAULT_ORG (default 'default', aligned to the loader
        # stamp in #200; was 'demo' before)
        assert await resolve_organization_id(x_api_key="unknown") == "default"
    with patch.dict("os.environ", {"API_KEY_ORGS": "", "DEFAULT_ORG": "tenantX"}, clear=False):
        assert await resolve_organization_id(x_api_key="whatever") == "tenantX"


# --- ingest: org comes from the key, NOT the body -------------------------

@pytest.mark.asyncio
async def test_ingest_uses_caller_org_and_reports_unmatched():
    from api.routes import provenance_decisions as mod

    fake = _FakeKG(about_rows=[
        {"tag": "parkinson disease", "matched": True},
        {"tag": "NCBITaxon:999-bogus", "matched": False},
    ])
    event = mod.DecisionEvent(
        id="dec-x", tool="nexus", action_type="committee_gate",
        occurred_at="2026-06-08T19:00:00Z", summary="GO",
        entity_tags=["parkinson disease", "NCBITaxon:999-bogus"],
    )
    with patch.object(mod, "get_kg", return_value=fake):
        result = await mod.ingest_decision(event, organization_id="acme")

    assert result.organization_id == "acme"
    assert result.entity_tags_matched == 1
    assert result.entity_tags_unmatched == ["NCBITaxon:999-bogus"]
    # the MERGE wrote organization_id = the caller's org (not anything from the body)
    merge_query, merge_params = fake.calls[0]
    assert "MERGE (d:Decision {id: $id})" in merge_query
    assert merge_params["organization_id"] == "acme"


def test_decision_event_has_no_organization_id_field():
    """organization_id must NOT be a client-settable field — it's auth-derived."""
    from api.routes.provenance_decisions import DecisionEvent
    assert "organization_id" not in DecisionEvent.model_fields


# --- query / ledger: scoped to the caller's org ---------------------------

@pytest.mark.asyncio
async def test_query_scopes_to_caller_org():
    from api.routes import provenance_decisions as mod
    fake = _FakeKG(query_rows=[_decision_row()])
    with patch.object(mod, "get_kg", return_value=fake):
        result = await mod.query_decisions(mod.DecisionQuery(entity="parkinson"),
                                           organization_id="acme")
    assert result.count == 1
    _, params = fake.calls[0]
    assert params["organization_id"] == "acme"     # not 'demo', not from body


@pytest.mark.asyncio
async def test_ledger_scopes_to_caller_org():
    from api.routes import provenance_decisions as mod
    fake = _FakeKG(ledger_rows=[
        {"source": "mapforge", "kind": "data_contribution", "summary": "panel",
         "occurred_at": "2026-06-07T00:00:00Z"},
    ])
    with patch.object(mod, "get_kg", return_value=fake):
        result = await mod.provenance_ledger(mod.LedgerQuery(entity="parkinson"),
                                             organization_id="acme")
    assert result.count == 1
    _, params = fake.calls[0]
    assert params["organization_id"] == "acme"


def test_query_and_ledger_bodies_have_no_org_field():
    from api.routes.provenance_decisions import DecisionQuery, LedgerQuery
    assert "organization_id" not in DecisionQuery.model_fields
    assert "organization_id" not in LedgerQuery.model_fields


# --- decision-side link to the spine :Analysis (#258) ----------------------

def test_decision_event_accepts_recorded_by_analysis_ids():
    from api.routes.provenance_decisions import DecisionEvent
    ev = DecisionEvent(id="d1", tool="nexus", action_type="hypothesis_verdict",
                       occurred_at="2026-06-08T00:00:00Z", summary="s",
                       recorded_by_analysis_ids=["an1", "an2"])
    assert ev.recorded_by_analysis_ids == ["an1", "an2"]


def test_decision_event_recorded_by_defaults_empty():
    from api.routes.provenance_decisions import DecisionEvent
    ev = DecisionEvent(id="d1", tool="nexus", action_type="committee_gate",
                       occurred_at="2026-06-08T00:00:00Z", summary="s")
    assert ev.recorded_by_analysis_ids == []


@pytest.mark.asyncio
async def test_ingest_links_recorded_analyses_and_counts():
    from api.routes import provenance_decisions as mod
    fake = _FakeKG(recorded_rows=[
        {"aid": "an1", "linked": True},
        {"aid": "an-missing", "linked": False},
    ])
    event = mod.DecisionEvent(
        id="dec-y", tool="nexus", action_type="hypothesis_verdict",
        occurred_at="2026-06-08T19:00:00Z", summary="verdict",
        recorded_by_analysis_ids=["an1", "an-missing"])
    with patch.object(mod, "get_kg", return_value=fake):
        result = await mod.ingest_decision(event, organization_id="acme")
    assert result.recorded_links_created == 1
    # the link step ran, org-scoped from the key
    link_calls = [c for c in fake.calls if "an IS NOT NULL AS linked" in c[0]]
    assert len(link_calls) == 1
    assert link_calls[0][1]["org"] == "acme"
    assert link_calls[0][1]["aids"] == ["an1", "an-missing"]
