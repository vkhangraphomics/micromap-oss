from micromap_mapforge.confidence import Confidence
from micromap_mapforge.resolve.base import Candidate, ResolutionRow


def test_candidate_fields():
    c = Candidate(
        node_id="NCBI:562",
        match_type=Confidence.EXTRACTED,
        score=1.0,
        reason="exact ncbi_tax_id match",
        properties={"scientific_name": "Escherichia coli"},
    )
    assert c.node_id == "NCBI:562"
    assert c.match_type == Confidence.EXTRACTED
    assert c.score == 1.0
    assert c.properties["scientific_name"] == "Escherichia coli"


def test_resolution_row_accepts_candidates():
    row = ResolutionRow(
        entity_label="Taxon",
        source_term="562",
        candidates=[
            Candidate("NCBI:562", Confidence.EXTRACTED, 1.0, "exact", {}),
        ],
    )
    assert row.best.match_type == Confidence.EXTRACTED
    assert row.is_resolved


def test_resolution_row_unresolved():
    row = ResolutionRow(entity_label="Taxon", source_term="9999", candidates=[])
    assert row.best is None
    assert not row.is_resolved


def test_resolution_row_ambiguous():
    row = ResolutionRow(
        entity_label="Disease",
        source_term="cancer",
        candidates=[
            Candidate("DOID:162", Confidence.INFERRED, 0.6, "fuzzy", {}),
            Candidate("DOID:14566", Confidence.INFERRED, 0.6, "fuzzy", {}),
        ],
    )
    # Two candidates at equal score → AMBIGUOUS
    assert row.best.match_type == Confidence.AMBIGUOUS
