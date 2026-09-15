"""Tests for column-name normalization in the heuristic mapper (GH#72).

Real source schemas don't use the exact identifier strings declared in
`ontology.yaml`. The heuristic mapper must match `ncbi_taxid` ↔
`ncbi_tax_id`, `Gene Symbol` ↔ `gene_symbol`, etc., by stripping
non-alphanumeric chars and lowercasing on both sides.

Without this, `--mode heuristic` produces an empty `entities: []` and
the validator rejects its own output — the offline-safe path was
unusable on any source whose columns aren't a literal substring of
the ontology identifiers."""

from micromap_mapforge.inspect.types import ColumnProfile, SourceProfile
from micromap_mapforge.mapping.mapper import draft_heuristic_mapping


def _profile(columns: list[str]) -> SourceProfile:
    cols = [
        ColumnProfile(name=c, inferred_type="string", null_rate=0.0,
                      distinct_count=10, samples=[])
        for c in columns
    ]
    return SourceProfile(path="x.tsv", format="tsv",
                         row_count_estimate=10, columns=cols)


def test_heuristic_matches_ncbi_taxid_to_ontology_ncbi_tax_id():
    """GTDB-style 'ncbi_taxid' (no underscore between tax+id) must match
    the ontology's 'ncbi_tax_id' identifier."""
    profile = _profile(["accession", "ncbi_taxid", "ncbi_organism_name"])
    mapping = draft_heuristic_mapping(profile)
    taxa = [e for e in mapping["entities"] if e["label"] == "Taxon"]
    assert taxa, "expected a Taxon entity from heuristic mapping"
    cols = taxa[0]["columns"]
    # The ontology field is 'ncbi_tax_id'; the source column is 'ncbi_taxid'.
    assert cols.get("ncbi_tax_id") == "ncbi_taxid", cols


def test_heuristic_matches_ncbi_organism_name_to_taxon_name_field():
    """Common-columns hint includes 'organism'; 'ncbi_organism_name' must
    normalize to a hint match (contains 'organism' as a sub-token)."""
    profile = _profile(["accession", "ncbi_taxid", "ncbi_organism_name"])
    mapping = draft_heuristic_mapping(profile)
    taxa = [e for e in mapping["entities"] if e["label"] == "Taxon"]
    assert taxa
    cols = taxa[0]["columns"]
    assert cols.get("name") == "ncbi_organism_name", cols


def test_heuristic_handles_camelcase_and_hyphen_variants():
    """ncbiTaxId and ncbi-tax-id must both normalize to the canonical id."""
    for variant in ("ncbiTaxId", "ncbi-tax-id", "NCBI_TAX_ID", "ncbi  tax  id"):
        profile = _profile([variant])
        mapping = draft_heuristic_mapping(profile)
        taxa = [e for e in mapping["entities"] if e["label"] == "Taxon"]
        assert taxa, f"variant {variant!r} did not match"
        assert taxa[0]["columns"].get("ncbi_tax_id") == variant
