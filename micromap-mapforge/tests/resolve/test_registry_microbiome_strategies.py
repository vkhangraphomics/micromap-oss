"""#202: the baked microbiome template declares explicit resolver strategies so
Disease/Paper keep their tuned resolvers (bit-identical to pre-#202 behavior)
while selection is now config-driven."""

from unittest.mock import MagicMock

from micromap_mapforge.resolve.registry import build_resolvers
from micromap_mapforge.resolve.disease import DiseaseResolver
from micromap_mapforge.resolve.paper import PaperResolver
from micromap_mapforge.resolve.generic import GenericResolver


def _fake_driver(records_by_label):
    driver = MagicMock()
    ctx = MagicMock()
    session = MagicMock()
    driver.session.return_value = ctx
    ctx.__enter__.return_value = session
    ctx.__exit__.return_value = False

    def run(cypher, **kw):
        for label, rows in records_by_label.items():
            if f"(n:{label})" in cypher:
                return [dict(r) for r in rows]
        return []

    session.run = run
    return driver


def test_baked_microbiome_disease_paper_use_tuned_resolvers():
    driver = _fake_driver({
        "Disease": [{"name_normalized": "crohn disease", "name": "Crohn disease"}],
        "Paper": [{"paper_id": "PMID:1", "title": "A study"}],
        "Taxon": [{"taxon_id": "NCBITaxon:1", "name": "root"}],
    })
    resolvers = build_resolvers(
        driver, database="neo4j",
        labels_of_interest={"Disease", "Paper", "Taxon"},
    )  # schema_config=None -> baked microbiome default
    assert isinstance(resolvers["Disease"], DiseaseResolver)
    assert isinstance(resolvers["Paper"], PaperResolver)
    assert isinstance(resolvers["Taxon"], GenericResolver)
