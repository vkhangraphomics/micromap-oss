from micromap_mapforge.confidence import Confidence
from micromap_mapforge.resolve.base import Candidate
from micromap_mapforge.resolve.pipeline import resolve_mapping, ResolutionReport


class StubResolver:
    def __init__(self, label, results_by_term):
        self.label = label
        self.results_by_term = results_by_term

    def resolve(self, term, context):
        return self.results_by_term.get(str(term), [])


def test_resolve_mapping_produces_report_per_entity():
    mapping = {
        "source": {"name": "s", "format": "csv", "path": "s.csv"},
        "entities": [
            {
                "label": "Taxon",
                "match_on": "ncbi_tax_id",
                "columns": {"ncbi_tax_id": "tax_id"},
                "confidence": "EXTRACTED",
            },
            {
                "label": "Disease",
                "match_on": "name_normalized",
                "columns": {"name": "condition"},
                "confidence": "EXTRACTED",
            },
        ],
        "relationships": [],
    }
    rows = [
        {"tax_id": "562", "condition": "Crohn's Disease"},
        {"tax_id": "1496", "condition": "Unknown Condition"},
    ]
    resolvers = {
        "Taxon": StubResolver("Taxon", {
            "562":  [Candidate("NCBI:562", Confidence.EXTRACTED, 1.0, "", {})],
            "1496": [Candidate("NCBI:1496", Confidence.EXTRACTED, 1.0, "", {})],
        }),
        "Disease": StubResolver("Disease", {
            "Crohn's Disease": [Candidate("DOID:8778", Confidence.INFERRED, 0.95, "", {})],
            "Unknown Condition": [],
        }),
    }

    report = resolve_mapping(mapping, rows, resolvers)
    assert isinstance(report, ResolutionReport)
    assert report.resolved_count == 3     # 2 taxa + 1 disease
    assert report.unresolved_count == 1   # "Unknown Condition"
    unresolved_terms = [r.source_term for r in report.unresolved]
    assert "Unknown Condition" in unresolved_terms


def test_resolve_mapping_extracts_column_from_match_on_match():
    mapping = {
        "source": {"name": "s", "format": "csv", "path": "s.csv"},
        "entities": [
            {
                "label": "Taxon",
                "match_on": "ncbi_tax_id",
                "columns": {"ncbi_tax_id": "tax_id", "scientific_name": "organism"},
                "confidence": "EXTRACTED",
            },
        ],
        "relationships": [],
    }
    rows = [{"tax_id": "562", "organism": "Escherichia coli"}]
    resolvers = {
        "Taxon": StubResolver("Taxon", {
            "562": [Candidate("NCBI:562", Confidence.EXTRACTED, 1.0, "", {})],
        }),
    }
    report = resolve_mapping(mapping, rows, resolvers)
    assert report.resolved_count == 1
    # Source field passed to resolver should be the ontology field (ncbi_tax_id), not the CSV col
    assert report.resolved[0].entity_label == "Taxon"


def test_deduplicates_repeated_terms():
    mapping = {
        "source": {"name": "s", "format": "csv", "path": "s.csv"},
        "entities": [
            {"label": "Taxon", "match_on": "ncbi_tax_id",
             "columns": {"ncbi_tax_id": "tax_id"}, "confidence": "EXTRACTED"},
        ],
        "relationships": [],
    }
    rows = [{"tax_id": "562"}, {"tax_id": "562"}, {"tax_id": "562"}]
    resolvers = {
        "Taxon": StubResolver("Taxon", {
            "562": [Candidate("NCBI:562", Confidence.EXTRACTED, 1.0, "", {})],
        }),
    }
    report = resolve_mapping(mapping, rows, resolvers)
    # Same term resolved once despite 3 source rows
    assert report.resolved_count == 1
