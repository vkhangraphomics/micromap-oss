from micromap_mapforge.confidence import Confidence
from micromap_mapforge.resolve.generic import GenericResolver


def make_resolver():
    return GenericResolver(
        label="Taxon",
        identifier_fields=["ncbi_tax_id", "gtdb_id"],
        name_field="scientific_name",
        primary_id_field="ncbi_tax_id",
        # Preloaded index: {identifier_field: {id_value: node_props}}
        identifier_index={
            "ncbi_tax_id": {
                "562": {"node_id": "NCBI:562", "scientific_name": "Escherichia coli", "ncbi_tax_id": "562"},
                "1496": {"node_id": "NCBI:1496", "scientific_name": "Clostridioides difficile", "ncbi_tax_id": "1496"},
            },
            "gtdb_id": {},
        },
        # Preloaded name index: {lowercased_name: [node_props]}
        name_index={
            "escherichia coli": [
                {"node_id": "NCBI:562", "scientific_name": "Escherichia coli", "ncbi_tax_id": "562"},
            ],
        },
    )


def test_resolver_label():
    assert make_resolver().label == "Taxon"


def test_exact_identifier_match_returns_extracted():
    r = make_resolver()
    cands = r.resolve("562", {"source_field": "ncbi_tax_id"})
    assert len(cands) == 1
    assert cands[0].match_type == Confidence.EXTRACTED
    assert cands[0].node_id == "ncbi_tax_id:562"
    assert cands[0].merge_field == "ncbi_tax_id"
    assert cands[0].merge_value == "562"
    assert cands[0].score == 1.0


def test_exact_name_match_returns_inferred():
    r = make_resolver()
    cands = r.resolve("Escherichia coli", {"source_field": "scientific_name"})
    assert len(cands) == 1
    assert cands[0].match_type == Confidence.INFERRED
    assert cands[0].node_id == "ncbi_tax_id:562"
    assert cands[0].merge_field == "ncbi_tax_id"
    assert cands[0].merge_value == "562"


def test_unknown_term_returns_empty():
    r = make_resolver()
    assert r.resolve("9999", {"source_field": "ncbi_tax_id"}) == []
    assert r.resolve("Klingon bacteria", {"source_field": "scientific_name"}) == []


def test_name_match_is_case_insensitive():
    r = make_resolver()
    cands = r.resolve("ESCHERICHIA COLI", {"source_field": "scientific_name"})
    assert len(cands) == 1
    assert cands[0].match_type == Confidence.INFERRED


def test_unknown_source_field_returns_empty():
    r = make_resolver()
    # source_field not in identifier_fields and not the name_field → empty
    assert r.resolve("562", {"source_field": "unknown_column"}) == []
