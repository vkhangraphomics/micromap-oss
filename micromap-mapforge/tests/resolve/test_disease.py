from micromap_mapforge.confidence import Confidence
from micromap_mapforge.resolve.disease import DiseaseResolver


def make_resolver(threshold: float = 0.85):
    return DiseaseResolver(
        identifier_fields=["doid", "mesh_id", "omim_id", "umls_cui", "icd10"],
        primary_id_field="doid",
        identifier_index={
            "doid": {
                "8778": {"node_id": "DOID:8778", "name": "Crohn's Disease", "doid": "8778"},
            },
            "mesh_id": {},
            "omim_id": {},
            "umls_cui": {},
            "icd10": {},
        },
        # Name index keyed by normalized name
        name_index_normalized={
            "crohns disease": [{"node_id": "DOID:8778", "name": "Crohn's Disease", "doid": "8778"}],
            "ulcerative colitis": [{"node_id": "DOID:8577", "name": "Ulcerative Colitis", "doid": "8577"}],
            "type 2 diabetes": [{"node_id": "DOID:9352", "name": "Type 2 Diabetes", "doid": "9352"}],
        },
        fuzzy_threshold=threshold,
    )


def test_label():
    assert make_resolver().label == "Disease"


def test_identifier_match_extracted():
    r = make_resolver()
    cands = r.resolve("8778", {"source_field": "doid"})
    assert len(cands) == 1
    assert cands[0].match_type == Confidence.EXTRACTED
    assert cands[0].node_id == "doid:8778"
    assert cands[0].merge_field == "doid"
    assert cands[0].merge_value == "8778"


def test_normalized_exact_match_inferred():
    r = make_resolver()
    # "Crohn's Disease" normalizes to "crohns disease"
    cands = r.resolve("Crohn's Disease", {"source_field": "name"})
    assert len(cands) >= 1
    assert cands[0].match_type == Confidence.INFERRED
    assert cands[0].node_id == "doid:8778"
    assert cands[0].merge_field == "doid"
    assert cands[0].merge_value == "8778"


def test_abbreviation_match_inferred():
    r = make_resolver()
    # "CD" → DISEASE_ABBREVIATIONS expands to "crohn's disease" → normalizes to "crohns disease"
    cands = r.resolve("CD", {"source_field": "name"})
    assert len(cands) >= 1
    assert cands[0].match_type == Confidence.INFERRED
    assert cands[0].node_id == "doid:8778"


def test_t2d_abbreviation():
    r = make_resolver()
    cands = r.resolve("T2D", {"source_field": "name"})
    assert len(cands) >= 1
    assert cands[0].node_id == "doid:9352"


def test_fuzzy_above_threshold_inferred():
    r = make_resolver(threshold=0.85)
    # "Ulcerative Colities" (typo) → normalized "ulcerative colities" ~ "ulcerative colitis"
    cands = r.resolve("Ulcerative Colities", {"source_field": "name"})
    assert len(cands) >= 1
    assert cands[0].match_type == Confidence.INFERRED
    assert "fuzzy" in cands[0].reason.lower() or "normalized" in cands[0].reason.lower()


def test_fuzzy_below_threshold_empty():
    r = make_resolver(threshold=0.95)   # very strict
    cands = r.resolve("xxx yyy zzz", {"source_field": "name"})
    assert cands == []


def test_unknown_source_field_empty():
    r = make_resolver()
    assert r.resolve("Crohn's Disease", {"source_field": "random_col"}) == []
