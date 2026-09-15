from micromap_mapforge.normalize import (
    DISEASE_ABBREVIATIONS,
    generate_disease_id,
    normalize_disease_name,
)


def test_normalize_lowercases():
    assert normalize_disease_name("Crohn's Disease") == "crohns disease"
    assert normalize_disease_name("ULCERATIVE COLITIS") == "ulcerative colitis"


def test_normalize_expands_known_abbreviations():
    assert normalize_disease_name("IBD") == "inflammatory bowel disease"
    assert normalize_disease_name("mdd") == "major depressive disorder"
    assert normalize_disease_name("T2D") == "type 2 diabetes"


def test_normalize_strips_apostrophes_and_hyphens():
    assert normalize_disease_name("Alzheimer's") == "alzheimers"
    assert normalize_disease_name("Non-Alcoholic Fatty Liver Disease") == "non alcoholic fatty liver disease"


def test_normalize_empty():
    assert normalize_disease_name("") == ""
    assert normalize_disease_name("   ") == ""


def test_generate_id_uses_canonical_when_available():
    assert generate_disease_id("Crohn's", {"doid": "8778"}) == "DOID:8778"
    assert generate_disease_id("IBD", {"mesh_id": "D015212"}) == "MESH:D015212"
    assert generate_disease_id("x", {"omim_id": "123"}) == "OMIM:123"


def test_generate_id_priority_order():
    ids = {"doid": "1", "mesh_id": "2", "omim_id": "3", "umls_cui": "4", "icd10": "K50"}
    assert generate_disease_id("x", ids) == "DOID:1"


def test_generate_id_falls_back_to_name():
    assert generate_disease_id("Crohn's Disease", None) == "disease:crohns_disease"
    assert generate_disease_id("Crohn's Disease", {}) == "disease:crohns_disease"


def test_abbreviations_dict_is_exposed():
    assert "ibd" in DISEASE_ABBREVIATIONS
    assert DISEASE_ABBREVIATIONS["crc"] == "colorectal cancer"
