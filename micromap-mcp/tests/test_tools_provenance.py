import asyncio
import inspect
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from micromap_mcp.auth import Principal
from micromap_mcp.scoping import OrgScope
from micromap_mcp.tools import provenance as mod
from micromap_mcp.tools.provenance import (
    _finding_from_payload,
    _lineage_cypher,
    canonical_orgs,
    register_provenance_tools,
)


@contextmanager
def _as_principal(principal):
    """Patch `current_principal` everywhere provenance.py's tools consult it.

    Since #299 final review item 6, org derivation goes through
    `auth.principal_org()`, which calls `auth.current_principal()` in auth.py's
    OWN namespace — a separate binding from `provenance.py`'s
    `from ..auth import current_principal` (still called directly for the
    `provenance_lineage` role gate). Patching only one target leaves the other
    call site seeing the real, unauthenticated principal, so both must be
    patched together to fake a single consistent caller.
    """
    with ExitStack() as stack:
        stack.enter_context(patch("micromap_mcp.tools.provenance.current_principal",
                                  return_value=principal))
        stack.enter_context(patch("micromap_mcp.auth.current_principal",
                                  return_value=principal))
        yield


def test_finding_from_payload_maps_fields():
    payload = dict(
        experiment={"id": "exp1", "title": "PD", "nexus_ref": "nx://1"},
        analysis={"id": "an1", "method": "agent-reasoning", "occurred_at": "2026-06-30T00:00:00",
                  "summary": "x up in PD"},
        assertions=[{
            "subject": {"label": "Taxon", "key_field": "ncbi_tax_id", "value": "209879"},
            "predicate": "ASSOCIATED_WITH_DISEASE",
            "object": {"label": "Disease", "key_field": "name_normalized", "value": "parkinson disease"},
            "confidence": 0.82, "direction": "increased",
            "valid_from": "2026-06-30T00:00:00", "supersedes": [],
        }],
        organization_id="org1",
    )
    rec = _finding_from_payload(payload)
    assert rec.experiment.id == "exp1"
    assert rec.analysis.id == "an1"
    assert rec.assertions[0].subject.value == "209879"
    assert rec.assertions[0].confidence == 0.82
    assert rec.assertions[0].organization_id == "org1"  # inherited from top-level

    # JSON key "object" must map to the dataclass field `object_` (reserved-name
    # dodge) -- and NOT bleed into `subject`.
    assert rec.assertions[0].object_.label == "Disease"
    assert rec.assertions[0].object_.key_field == "name_normalized"
    assert rec.assertions[0].object_.value == "parkinson disease"
    assert rec.assertions[0].subject.value != rec.assertions[0].object_.value

    # `asserted_at` is omitted on the assertion row above, so it must fall back
    # to the analysis's `occurred_at`.
    assert rec.assertions[0].asserted_at == rec.analysis.occurred_at


def test_lineage_cypher_current_and_as_of_differ():
    from micromap_mcp.tools.provenance import _lineage_cypher
    cur, _ = _lineage_cypher(None)
    asof, params = _lineage_cypher("2026-03-01T00:00:00")
    assert "SUPERSEDED_BY" in cur
    assert "valid_from" in asof and params["as_of"] == "2026-03-01T00:00:00"
    # Locks the RECORDED/decisions projection so a future drift from the REST
    # module's _TAIL (api/routes/provenance_lineage.py) is caught here (#258).
    assert "RECORDED" in cur
    assert "decisions" in cur


def test_lineage_cypher_filters_canonical_by_default():
    """Was: `a.organization_id IN $canonical_orgs`. Since #299 the predicate is
    the shared ORG_FILTER — caller's own org OR the canonical orgs — so the
    canonical set moved from $canonical_orgs to $public_orgs."""
    for as_of in (None, "2026-03-01T00:00:00"):
        cur, params = _lineage_cypher(as_of, entity="Butyrate")
        assert "a.organization_id = $organization_id" in cur
        assert "a.organization_id IN $public_orgs" in cur
        assert set(params["public_orgs"]) == set(canonical_orgs())


def test_lineage_cypher_include_noncanonical_drops_filter():
    """The BUILDER still honours the flag; the TOOL now decides who may set it.
    See test_include_noncanonical_is_ignored_for_non_privileged_callers (#299)."""
    cur, params = _lineage_cypher(None, entity="Butyrate", include_noncanonical=True)
    assert "a.organization_id IN $canonical_orgs" not in cur
    assert "canonical_orgs" not in params


def test_record_finding_threads_decision_ids(monkeypatch):
    captured = {}

    def fake_write_finding(driver, rec, database="neo4j", link_decision_ids=None):
        captured["link"] = link_decision_ids
        captured["db"] = database
        return {"experiment_id": rec.experiment.id, "recorded_links": len(link_decision_ids or [])}

    monkeypatch.setattr(mod, "write_finding", fake_write_finding)
    tools = mod.register_provenance_tools(
        app=None, neo4j_uri="bolt://localhost:7687", neo4j_user="u",
        neo4j_password="p", database="neo4j", return_callables=True)

    out = asyncio.run(tools["provenance_record_finding"](
        experiment={"id": "e1"},
        analysis={"id": "an1", "occurred_at": "2026-06-30T00:00:00"},
        assertions=[],
        decision_ids=["dec-1"]))

    assert captured["link"] == ["dec-1"]
    assert out["recorded_links"] == 1


# ---------------------------------------------------------------------------
# #299: org comes from the authenticated principal, never the caller
# ---------------------------------------------------------------------------


def test_record_finding_no_longer_accepts_organization_id():
    tools = register_provenance_tools(app=None, neo4j_uri="bolt://x", neo4j_user="u",
                                      neo4j_password="p", database="neo4j",
                                      return_callables=True)
    sig = inspect.signature(tools["provenance_record_finding"])
    assert "organization_id" not in sig.parameters


def test_include_noncanonical_is_ignored_for_non_privileged_callers():
    """It was a caller-flipped boolean that dropped the only org filter."""
    tools = register_provenance_tools(app=None, neo4j_uri="bolt://x", neo4j_user="u",
                                      neo4j_password="p", database="neo4j",
                                      return_callables=True)
    with _as_principal(Principal(org_id="acme", role="user")):
        with patch("micromap_mcp.tools.provenance.GraphDatabase"):
            with patch("micromap_mcp.tools.provenance._lineage_cypher",
                       wraps=_lineage_cypher) as spy:
                asyncio.run(tools["provenance_lineage"]("Butyrate", include_noncanonical=True))
    assert spy.call_args.kwargs["include_noncanonical"] is False


def test_include_noncanonical_is_honoured_for_service_callers():
    tools = register_provenance_tools(app=None, neo4j_uri="bolt://x", neo4j_user="u",
                                      neo4j_password="p", database="neo4j",
                                      return_callables=True)
    with _as_principal(Principal(org_id="graphomics", role="service")):
        with patch("micromap_mcp.tools.provenance.GraphDatabase"):
            with patch("micromap_mcp.tools.provenance._lineage_cypher",
                       wraps=_lineage_cypher) as spy:
                asyncio.run(tools["provenance_lineage"]("Butyrate", include_noncanonical=True))
    assert spy.call_args.kwargs["include_noncanonical"] is True


def test_lineage_tool_wires_callers_org_into_scope():
    """The actual integration point (Finding 2, #299): `provenance_lineage`
    must build `scope=OrgScope(org_id=<caller's org>, public_orgs=<canonical
    orgs>)` and pass it through to `_lineage_cypher` as the `scope` kwarg — not
    just `include_noncanonical`. The three builder-level tests above call
    `_lineage_cypher` directly with a hand-built `OrgScope` and never exercise
    this wiring; a dropped kwarg or wrong attribute here would pass all of
    them while silently widening (or narrowing) every tenant's visibility."""
    tools = register_provenance_tools(app=None, neo4j_uri="bolt://x", neo4j_user="u",
                                      neo4j_password="p", database="neo4j",
                                      return_callables=True)
    with _as_principal(Principal(org_id="acme", role="user")):
        with patch("micromap_mcp.tools.provenance.GraphDatabase"):
            with patch("micromap_mcp.tools.provenance._lineage_cypher",
                       wraps=_lineage_cypher) as spy:
                asyncio.run(tools["provenance_lineage"]("Butyrate"))
    scope = spy.call_args.kwargs["scope"]
    assert isinstance(scope, OrgScope)
    assert scope.org_id == "acme"
    assert set(scope.public_orgs) == set(canonical_orgs())


def test_lineage_tool_wires_default_org_into_scope_when_unauthenticated():
    """No principal must resolve to the default org, not an empty/missing
    scope — the same never-empty guarantee `provenance_record_finding` relies
    on (Finding 1, #299), now checked on the read side's wiring too."""
    tools = register_provenance_tools(app=None, neo4j_uri="bolt://x", neo4j_user="u",
                                      neo4j_password="p", database="neo4j",
                                      return_callables=True)
    with _as_principal(None):
        with patch("micromap_mcp.tools.provenance.GraphDatabase"):
            with patch("micromap_mcp.tools.provenance._lineage_cypher",
                       wraps=_lineage_cypher) as spy:
                asyncio.run(tools["provenance_lineage"]("Butyrate"))
    scope = spy.call_args.kwargs["scope"]
    from micromap_mcp.auth import default_org
    assert isinstance(scope, OrgScope)
    assert scope.org_id == default_org()
    assert scope.org_id


def _payload_with_one_assertion():
    return dict(
        experiment={"id": "e1"},
        analysis={"id": "an1", "occurred_at": "2026-06-30T00:00:00"},
        assertions=[{
            "subject": {"label": "Taxon", "key_field": "ncbi_tax_id", "value": "209879"},
            "predicate": "ASSOCIATED_WITH_DISEASE",
            "object": {"label": "Disease", "key_field": "name_normalized", "value": "parkinson disease"},
            "confidence": 0.82,
        }],
    )


def test_record_finding_stamps_the_principals_org_including_assertions():
    """The org actually written must be the authenticated principal's org —
    not just threaded through as a top-level argument (Finding 1, #299)."""
    captured = {}

    def fake_write_finding(driver, rec, database="neo4j", link_decision_ids=None):
        captured["rec"] = rec
        return {"experiment_id": rec.experiment.id, "recorded_links": 0}

    tools = register_provenance_tools(app=None, neo4j_uri="bolt://x", neo4j_user="u",
                                      neo4j_password="p", database="neo4j",
                                      return_callables=True)
    with patch("micromap_mcp.tools.provenance.write_finding", fake_write_finding):
        with _as_principal(Principal(org_id="acme-tenant", role="user")):
            asyncio.run(tools["provenance_record_finding"](**_payload_with_one_assertion()))

    rec = captured["rec"]
    assert rec.experiment.organization_id == "acme-tenant"
    assert rec.analysis.organization_id == "acme-tenant"
    assert len(rec.assertions) == 1
    assert rec.assertions[0].organization_id == "acme-tenant"


def test_record_finding_falls_back_to_default_org_when_unauthenticated():
    """No principal (unauthenticated / static-token-without-map caller) must
    still stamp a non-empty org — the never-empty guarantee the `or
    default_org()` fallback depends on (Finding 1 / Finding 3, #299). A NULL
    or empty organization_id is the exact shape of the #269 incident: 139
    nodes silently invisible to the org-filtered API."""
    captured = {}

    def fake_write_finding(driver, rec, database="neo4j", link_decision_ids=None):
        captured["rec"] = rec
        return {"experiment_id": rec.experiment.id, "recorded_links": 0}

    tools = register_provenance_tools(app=None, neo4j_uri="bolt://x", neo4j_user="u",
                                      neo4j_password="p", database="neo4j",
                                      return_callables=True)
    with patch("micromap_mcp.tools.provenance.write_finding", fake_write_finding):
        with _as_principal(None):
            asyncio.run(tools["provenance_record_finding"](**_payload_with_one_assertion()))

    rec = captured["rec"]
    from micromap_mcp.auth import default_org
    assert rec.experiment.organization_id == default_org()
    assert rec.experiment.organization_id
    assert rec.assertions[0].organization_id == default_org()


def test_lineage_is_visible_to_the_callers_own_org():
    """A tenant must see its OWN contributions, not only canonical reference."""
    scope = OrgScope(org_id="acme", public_orgs=["default"])
    cur, params = _lineage_cypher(None, entity="Butyrate", scope=scope)
    assert "a.organization_id = $organization_id" in cur
    assert params["organization_id"] == "acme"


def test_lineage_still_includes_canonical_orgs_as_public():
    from micromap_mcp.tools.provenance import canonical_orgs
    scope = OrgScope(org_id="acme", public_orgs=list(canonical_orgs()))
    _, params = _lineage_cypher(None, entity="Butyrate", scope=scope)
    assert set(params["public_orgs"]) == set(canonical_orgs())


def test_lineage_without_a_scope_is_canonical_only():
    """No principal must never widen to a tenant."""
    from micromap_mcp.tools.provenance import canonical_orgs
    _, params = _lineage_cypher(None, entity="Butyrate", scope=None)
    assert set(params["public_orgs"]) == set(canonical_orgs())
    assert params["organization_id"] == "default"
