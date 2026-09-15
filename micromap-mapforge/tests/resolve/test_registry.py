from unittest.mock import MagicMock

from micromap_mapforge.resolve.registry import build_resolvers


def _fake_session(records_by_label: dict[str, list[dict]]) -> MagicMock:
    """Build a MagicMock Neo4j session that returns different records per label.

    The registry queries: MATCH (n:{label}) RETURN properties(n) AS props
    """
    driver = MagicMock()
    session_ctx = MagicMock()
    session = MagicMock()
    driver.session.return_value = session_ctx
    session_ctx.__enter__ = MagicMock(return_value=session)
    session_ctx.__exit__ = MagicMock(return_value=False)

    def run(cypher, **kwargs):
        # Registry now projects fields flat: `RETURN n.id_a AS id_a, n.name AS name, ...`
        # The fake returns records as flat dicts (one record == one node's
        # projected fields). Extra fields on the test fixture are ignored
        # by the registry's `_fetch_nodes`, mirroring real driver behavior
        # where the projection determines what's pulled across the wire.
        for label in records_by_label:
            if f"(n:{label})" in cypher:
                rows = list(records_by_label[label])
                result = MagicMock()
                result.__iter__ = lambda self, _r=rows: iter(_r)
                result.data.return_value = rows
                return result
        result = MagicMock()
        result.__iter__ = lambda self: iter([])
        result.data.return_value = []
        return result

    session.run = run
    return driver


def test_build_resolvers_returns_one_per_node_type():
    driver = _fake_session(
        {
            "Taxon": [{"node_id": "NCBI:562", "ncbi_tax_id": "562", "scientific_name": "Escherichia coli"}],
            "Disease": [{"node_id": "DOID:8778", "doid": "8778", "name": "Crohn's Disease"}],
            "Paper":   [{"node_id": "PMID:12345", "pmid": "12345", "title": "Gut microbiome"}],
            "Metabolite": [], "Compound": [], "Drug": [], "Gene": [], "Protein": [],
            "Pathway": [], "BodySite": [], "Assay": [], "Study": [],
        }
    )
    resolvers = build_resolvers(driver, database="neo4j")
    assert set(resolvers.keys()) == {
        "Taxon", "Disease", "Compound", "Drug",
        "Gene", "Protein", "Pathway", "Paper", "BodySite",
        "Assay", "Study",
    }


def test_taxon_resolver_preloaded_and_usable():
    driver = _fake_session(
        {
            "Taxon": [
                {"node_id": "NCBI:562", "ncbi_tax_id": "562", "scientific_name": "Escherichia coli"},
            ],
            "Disease": [], "Metabolite": [], "Drug": [], "Gene": [],
            "Protein": [], "Pathway": [], "Paper": [], "BodySite": [],
        }
    )
    resolvers = build_resolvers(driver, database="neo4j")
    taxon = resolvers["Taxon"]
    cands = taxon.resolve("562", {"source_field": "ncbi_tax_id"})
    assert len(cands) == 1
    assert cands[0].node_id == "ncbi_tax_id:562"
    assert cands[0].merge_field == "ncbi_tax_id"
    assert cands[0].merge_value == "562"


def test_disease_resolver_uses_normalized_name_index():
    driver = _fake_session(
        {
            "Disease": [
                {"node_id": "DOID:8778", "doid": "8778", "name": "Crohn's Disease"},
            ],
            "Taxon": [], "Metabolite": [], "Drug": [], "Gene": [],
            "Protein": [], "Pathway": [], "Paper": [], "BodySite": [],
        }
    )
    resolvers = build_resolvers(driver, database="neo4j")
    d = resolvers["Disease"]
    cands = d.resolve("CD", {"source_field": "name"})   # abbreviation expansion via normalizer
    assert len(cands) >= 1
    assert cands[0].match_type.value in ("INFERRED", "EXTRACTED")
