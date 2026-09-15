from datetime import datetime
import pytest
from micromap_mapforge.provenance.spine import (
    EndpointRef, ExperimentRecord, AnalysisRecord, AssertionRecord, FindingRecord,
    cypher_for_finding, cypher_for_projection, cypher_for_parallel_projection,
    cypher_for_recorded_links,
)

def _finding():
    exp = ExperimentRecord(id="exp1", organization_id="org1", title="PD investigation")
    an = AnalysisRecord(id="an1", organization_id="org1", method="agent-reasoning",
                        occurred_at=datetime(2026, 6, 30), summary="genus x up in PD")
    a = AssertionRecord(
        predicate="ASSOCIATED_WITH_DISEASE",
        subject=EndpointRef("Taxon", "ncbi_tax_id", "209879"),
        object_=EndpointRef("Disease", "name_normalized", "parkinson disease"),
        confidence=0.82, organization_id="org1",
        asserted_at=datetime(2026, 6, 30), valid_from=datetime(2026, 6, 30),
        analysis_id="an1", direction="increased", evidence_refs=["PMID:1"],
        supersedes=["old-assertion-id"],
    )
    return FindingRecord(experiment=exp, analysis=an, assertions=[a])

def test_finding_statements_are_all_parameterized():
    for stmt, params in cypher_for_finding(_finding()):
        assert isinstance(params, dict)
        # no bare interpolation markers; the query must not embed the actual values
        assert "209879" not in stmt and "parkinson disease" not in stmt and "org1" not in stmt

def test_finding_includes_core_merges_and_links():
    joined = " ".join(s for s, _ in cypher_for_finding(_finding()))
    for token in ["MERGE (e:Experiment", "MERGE (an:Analysis", ":HAS_ANALYSIS",
                  "MERGE (a:Assertion", ":ASSERTED", ":SUBJECT", ":OBJECT", ":SUPERSEDED_BY"]:
        assert token in joined

def test_projection_rel_type_from_allowlist():
    stmt, _ = cypher_for_projection(
        "ASSOCIATED_WITH_DISEASE",
        EndpointRef("Taxon", "ncbi_tax_id", "209879"),
        EndpointRef("Disease", "name_normalized", "parkinson disease"),
        "org1",
    )
    assert "[r:ASSOCIATED_WITH_DISEASE]" in stmt
    assert "209879" not in stmt  # value is a param

def test_projection_rejects_unwired_predicate():
    with pytest.raises(ValueError):
        cypher_for_projection("MENTIONED_IN", EndpointRef("Taxon","taxon_id","t"),
                              EndpointRef("Paper","pmid","p"), "org1")


def test_projection_stamps_organization_id():
    # #284: the projected concrete edge must carry the asserting org so a future
    # edge-org-aware read can scope it; a NULL edge-org is what let rehearsal
    # edges masquerade as reference truth. The value stays a param.
    stmt, params = cypher_for_projection(
        "ASSOCIATED_WITH_DISEASE",
        EndpointRef("Taxon", "ncbi_tax_id", "209879"),
        EndpointRef("Disease", "name_normalized", "parkinson disease"),
        "org1",
    )
    assert "r.organization_id = $organization_id" in stmt
    assert params["organization_id"] == "org1"


# --- #297 Part B: parallel per-org edges --------------------------------------

def test_canonical_projection_only_matches_non_customer_edges():
    """#297 Part B: the canonical projection must NOT pattern-match a customer's
    parallel edge (org-stamped) and overwrite it — that would leak the customer's
    contribution to every caller. So it selects the edge to update by org: only a
    NULL-org (ingested) or canonical-org edge, never a customer one."""
    stmt, params = cypher_for_projection(
        "ASSOCIATED_WITH_DISEASE",
        EndpointRef("Taxon", "ncbi_tax_id", "209879"),
        EndpointRef("Disease", "name_normalized", "parkinson disease"),
        "default", canonical=frozenset({"default"}),
    )
    assert "existing.organization_id IS NULL" in stmt
    assert "existing.organization_id IN $canonical_orgs" in stmt
    assert params["canonical_orgs"] == ["default"]
    # still creates a fully-stamped edge when none exists
    assert "r.organization_id = $organization_id" in stmt
    assert "r.source = 'provenance-spine'" in stmt


def test_parallel_projection_keys_merge_on_org():
    """A non-canonical (customer) org projects a PARALLEL edge keyed on its org,
    so the MERGE creates a distinct edge instead of matching the shared one."""
    stmt, params = cypher_for_parallel_projection(
        "ASSOCIATED_WITH_DISEASE",
        EndpointRef("Taxon", "ncbi_tax_id", "209879"),
        EndpointRef("Disease", "name_normalized", "parkinson disease"),
        "acme",
    )
    assert "MERGE (s)-[r:ASSOCIATED_WITH_DISEASE {organization_id: $organization_id}]->(o)" in stmt
    assert params["organization_id"] == "acme"
    # a customer edge is always spine-owned; no ingested-source guard is needed
    assert "r.source = 'provenance-spine'" in stmt
    assert "209879" not in stmt and "acme" not in stmt  # values stay params


def test_parallel_projection_rejects_unwired_predicate():
    with pytest.raises(ValueError):
        cypher_for_parallel_projection(
            "MENTIONED_IN", EndpointRef("Taxon", "taxon_id", "t"),
            EndpointRef("Paper", "pmid", "p"), "acme")


# --- #259: index-eligible endpoint resolution ---------------------------------

def _finding_with(subject: EndpointRef, object_: EndpointRef, predicate="ASSOCIATED_WITH_DISEASE"):
    exp = ExperimentRecord(id="exp1", organization_id="org1")
    an = AnalysisRecord(id="an1", organization_id="org1", method="m",
                        occurred_at=datetime(2026, 6, 30))
    a = AssertionRecord(
        predicate=predicate, subject=subject, object_=object_,
        confidence=0.8, organization_id="org1",
        asserted_at=datetime(2026, 6, 30), valid_from=datetime(2026, 6, 30),
        analysis_id="an1",
    )
    return FindingRecord(experiment=exp, analysis=an, assertions=[a])


def test_finding_allowlisted_endpoints_emit_literal_index_seek():
    # Taxon/ncbi_tax_id and Disease/name_normalized are index-backed → the
    # SUBJECT/OBJECT resolution must emit a literal (:Label {key: $value}) so the
    # planner can NodeIndexSeek, not scan all nodes.
    joined = " ".join(s for s, _ in cypher_for_finding(_finding_with(
        EndpointRef("Taxon", "ncbi_tax_id", "209879"),
        EndpointRef("Disease", "name_normalized", "parkinson disease"),
    )))
    assert "(n:Taxon {ncbi_tax_id: $ep_value})" in joined
    assert "(n:Disease {name_normalized: $ep_value})" in joined
    # dynamic label scan must NOT be used for these endpoints
    assert "IN labels(n)" not in joined


def test_finding_unlisted_endpoints_fall_back_to_dynamic_scan():
    # A pair with no matching index keeps the resolve-if-it-exists dynamic scan.
    joined = " ".join(s for s, _ in cypher_for_finding(_finding_with(
        EndpointRef("Paper", "pmid", "PMID:1"),
        EndpointRef("Widget", "widget_id", "w1"),
        predicate="MENTIONED_IN",
    )))
    assert "IN labels(n)" in joined
    assert "(n:Paper" not in joined and "(n:Widget" not in joined


def test_finding_endpoint_values_are_never_literals():
    # Whichever path is taken, the endpoint VALUE stays a parameter.
    for stmt, _ in cypher_for_finding(_finding_with(
        EndpointRef("Taxon", "ncbi_tax_id", "209879"),
        EndpointRef("Disease", "name_normalized", "parkinson disease"),
    )):
        assert "209879" not in stmt and "parkinson disease" not in stmt


def test_projection_allowlisted_endpoints_emit_literal_index_seek():
    stmt, params = cypher_for_projection(
        "ASSOCIATED_WITH_DISEASE",
        EndpointRef("Taxon", "ncbi_tax_id", "209879"),
        EndpointRef("Disease", "name_normalized", "parkinson disease"),
        "org1",
    )
    assert "(s:Taxon {ncbi_tax_id: $s_value})" in stmt
    assert "(o:Disease {name_normalized: $o_value})" in stmt
    assert "IN labels(s)" not in stmt and "IN labels(o)" not in stmt
    assert "209879" not in stmt
    assert params["s_value"] == "209879" and params["o_value"] == "parkinson disease"


# --- #258: :Analysis -[:RECORDED]-> :Decision link builder --------------------

def test_recorded_links_none_when_no_ids():
    assert cypher_for_recorded_links("an1", [], "org1") is None


def test_recorded_links_is_parameterized_and_org_scoped():
    stmt, params = cypher_for_recorded_links("an1", ["dec-1", "dec-2"], "org1")
    assert "MERGE (an)-[:RECORDED]->(d)" in stmt
    assert "OPTIONAL MATCH (d:Decision {id: did, organization_id: $org})" in stmt
    # nothing interpolated
    assert "an1" not in stmt and "dec-1" not in stmt and "org1" not in stmt
    assert params == {"aid": "an1", "dids": ["dec-1", "dec-2"], "org": "org1"}
