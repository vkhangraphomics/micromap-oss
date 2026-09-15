"""#266: the mapping-quality scorer — compare a produced mapping.yaml against a
hand-curated gold mapping and report entity / column / relationship quality.

The scorer is pure and deterministic (no I/O, no LLM), so it runs in CI and is
the shared measuring stick for BOTH the heuristic and LLM arms.
"""
import math

from micromap_mapforge.evals.mapping_quality import score_mapping, MappingScore

# A small gold mapping with two entities and one relationship, in the real
# mapping.yaml shape (entities carry columns as {ontology_field: source_col}).
GOLD = {
    "source": {"name": "s", "format": "csv", "path": "s.csv"},
    "entities": [
        {"label": "Taxon", "match_on": "ncbi_tax_id",
         "columns": {"ncbi_tax_id": "ncbi_taxid", "scientific_name": "microorganism"},
         "confidence": "EXTRACTED"},
        {"label": "Disease", "match_on": "name_normalized",
         "columns": {"name": "disease"}, "confidence": "EXTRACTED"},
    ],
    "relationships": [
        {"type": "ASSOCIATED_WITH_DISEASE",
         "from": "Taxon(ncbi_tax_id=row.ncbi_taxid)",
         "to": "Disease(name_normalized=normalize(row.disease))",
         "confidence": "EXTRACTED"},
    ],
}


def _close(a, b):
    return math.isclose(a, b, abs_tol=1e-9)


def test_perfect_match_scores_one_everywhere():
    score = score_mapping(GOLD, GOLD)
    assert isinstance(score, MappingScore)
    for prf in (score.entities, score.columns, score.relationships):
        assert _close(prf.precision, 1.0) and _close(prf.recall, 1.0) and _close(prf.f1, 1.0)
    assert _close(score.match_on_accuracy, 1.0)


def test_missing_entity_label_lowers_entity_recall():
    produced = {**GOLD, "entities": [GOLD["entities"][0]]}  # drop Disease
    score = score_mapping(produced, GOLD)
    assert _close(score.entities.recall, 0.5)      # 1 of 2 gold labels
    assert _close(score.entities.precision, 1.0)   # the one it emitted is correct


def test_heuristic_style_empty_relationships_gives_zero_relationship_recall():
    produced = {**GOLD, "relationships": []}       # the heuristic never invents rels
    score = score_mapping(produced, GOLD)
    assert _close(score.relationships.recall, 0.0)
    assert _close(score.relationships.f1, 0.0)
    # entities/columns are unaffected by the missing relationships
    assert _close(score.entities.f1, 1.0)


def test_wrong_match_on_lowers_match_on_accuracy_only():
    bad = {"label": "Taxon", "match_on": "scientific_name",   # wrong key field
           "columns": {"ncbi_tax_id": "ncbi_taxid", "scientific_name": "microorganism"},
           "confidence": "INFERRED"}
    produced = {**GOLD, "entities": [bad, GOLD["entities"][1]]}
    score = score_mapping(produced, GOLD)
    assert _close(score.match_on_accuracy, 0.5)    # Taxon wrong, Disease right
    assert _close(score.columns.recall, 1.0)       # columns themselves still correct


def test_wrong_source_column_lowers_column_precision_and_recall():
    bad = {"label": "Taxon", "match_on": "ncbi_tax_id",
           "columns": {"ncbi_tax_id": "WRONG_COL", "scientific_name": "microorganism"},
           "confidence": "INFERRED"}
    produced = {**GOLD, "entities": [bad, GOLD["entities"][1]]}
    score = score_mapping(produced, GOLD)
    # 2 of 3 gold (label, field, col) triples reproduced (Taxon.ncbi_tax_id wrong).
    assert _close(score.columns.recall, 2 / 3)
    assert score.columns.precision < 1.0


def test_relationship_scored_by_type_and_endpoint_labels_not_properties():
    # Same (type, from-label, to-label) but different properties → still a match:
    # relationship quality is about the graph edge, not the property payload.
    rel = {"type": "ASSOCIATED_WITH_DISEASE",
           "from": "Taxon(ncbi_tax_id=row.other)",
           "to": "Disease(name_normalized=row.dz)",
           "properties": {"x": "y"}, "confidence": "INFERRED"}
    produced = {**GOLD, "relationships": [rel]}
    score = score_mapping(produced, GOLD)
    assert _close(score.relationships.f1, 1.0)


def test_extra_predicted_relationship_lowers_precision():
    extra = {"type": "PRODUCES", "from": "Taxon(x=row.a)", "to": "Compound(y=row.b)"}
    produced = {**GOLD, "relationships": GOLD["relationships"] + [extra]}
    score = score_mapping(produced, GOLD)
    assert _close(score.relationships.recall, 1.0)     # both gold rels present
    assert _close(score.relationships.precision, 0.5)  # 1 of 2 predicted is spurious
