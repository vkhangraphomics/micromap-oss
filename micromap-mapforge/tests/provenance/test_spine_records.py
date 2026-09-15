from datetime import datetime
from micromap_mapforge.provenance.spine import (
    EndpointRef, AssertionRecord, WIRED_PREDICATES,
)

def _assertion(analysis_id="an1", subj="209879", obj="parkinson disease"):
    return AssertionRecord(
        predicate="ASSOCIATED_WITH_DISEASE",
        subject=EndpointRef("Taxon", "ncbi_tax_id", subj),
        object_=EndpointRef("Disease", "name_normalized", obj),
        confidence=0.8, organization_id="org1",
        asserted_at=datetime(2026, 6, 30), valid_from=datetime(2026, 6, 30),
        analysis_id=analysis_id,
    )

def test_assertion_id_is_deterministic():
    assert _assertion().id == _assertion().id

def test_assertion_id_changes_with_triple():
    assert _assertion(subj="209879").id != _assertion(subj="99999").id

def test_assertion_id_changes_with_analysis():
    assert _assertion(analysis_id="an1").id != _assertion(analysis_id="an2").id

def test_wired_predicates_cover_demo_edges():
    assert WIRED_PREDICATES["ASSOCIATED_WITH_DISEASE"] == ("Taxon", "Disease")
    assert "PRODUCES" in WIRED_PREDICATES
