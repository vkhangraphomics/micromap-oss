"""#273: 331,131 taxa (29%) have no HAS_PARENT edge.

Root cause is an ORDERING bug, not the partial load the issue first suspected.

`load_batch` writes a batch's nodes and then immediately links that batch's
parent edges, and `_load_parent_relationships` resolves the parent with

    MATCH (parent:Taxon {taxon_id: record.to_id})

A parent that appears in a LATER batch does not exist yet, so the UNWIND row is
silently dropped — no error, no retry, and `BaseLoader.run` has no second pass.

Live evidence that this is the mechanism: of the 811,720 edges that DO exist,
807,968 (99.5%) have a parent tax_id lower than the child's, i.e. the parent
happened to be written first. `Faecalibacterium prausnitzii` is the famous
casualty — child 853, parent genus 216851 — the parent sorts ~216k records
later, so the edge was never created even though both nodes are present.

Note the two clues the issue reasoned from (`parent_id` null, `source` null) do
not discriminate: verified live, ALL 1,142,851 taxa have both null, because the
node write never persisted either property. That is fixed here too — persisting
`parent_taxon_id` is what lets the linkage be re-derived in place.
"""
import re
from unittest.mock import MagicMock

import pytest

from database.ingestion.ncbi_taxonomy_loader import NCBITaxonomyLoader


class FakeGraph:
    """Simulates the two writes with real MATCH-both-ends semantics.

    An edge is only created when BOTH endpoints already exist — which is
    precisely the behaviour that loses edges when the parent lands later.
    """

    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: set[tuple[str, str]] = set()

    def execute_cypher(self, query: str, params: dict | None = None):
        params = params or {}
        if "MERGE (t:Taxon" in query:
            # Only persist properties the query ACTUALLY assigns. Storing the
            # whole record here would make the persistence test assert on this
            # fake rather than on the loader's Cypher.
            assigned = set(re.findall(r"t\.(\w+)\s*=\s*record\.(\w+)", query))
            keys = {record_key for _, record_key in assigned}
            for rec in params.get("batch", []):
                node = self.nodes.setdefault(rec["taxon_id"], {"taxon_id": rec["taxon_id"]})
                node.update({k: rec[k] for k in keys if rec.get(k) is not None})
            return [{"count": len(params.get("batch", []))}]

        if "HAS_PARENT" in query and "MERGE" in query:
            created = 0
            # the finalize pass drives off stored properties, not a batch
            records = params.get("records")
            if records is None:
                records = [
                    {"from_id": tid, "to_id": n["parent_taxon_id"]}
                    for tid, n in self.nodes.items()
                    if n.get("parent_taxon_id")
                ]
            for rec in records:
                if rec["from_id"] in self.nodes and rec["to_id"] in self.nodes:
                    if (rec["from_id"], rec["to_id"]) not in self.edges:
                        self.edges.add((rec["from_id"], rec["to_id"]))
                        created += 1
            return [{"count": created}]

        return []


@pytest.fixture
def loader():
    ldr = NCBITaxonomyLoader.__new__(NCBITaxonomyLoader)
    ldr.organization_id = "default"
    ldr.stats = MagicMock()
    ldr.database = "neo4j"
    ldr.batch_size = 1000
    graph = FakeGraph()
    ldr.execute_cypher = graph.execute_cypher
    ldr._graph = graph
    return ldr


def _rec(tax_id: str, parent: str | None, name: str, rank: str = "species"):
    """A transformed record, matching what `transform()` produces."""
    return {
        "taxon_id": f"NCBITaxon:{tax_id}",
        "ncbi_tax_id": tax_id,
        "name": name,
        "common_name": None,
        "rank": rank,
        "parent_taxon_id": f"NCBITaxon:{parent}" if parent else None,
        "kingdom": None, "phylum": None, "class": None, "order": None,
        "family": None, "genus": None, "species": None,
        "division_id": None, "genetic_code_id": None,
        "organization_id": "default",
    }


# The real F. prausnitzii shape: child 853, parent genus 216851.
CHILD = _rec("853", "216851", "Faecalibacterium prausnitzii")
PARENT = _rec("216851", "1", "Faecalibacterium", rank="genus")
ROOT = _rec("1", None, "root", rank="no rank")


class TestParentEdgeSurvivesBatchOrdering:
    def test_child_loaded_before_its_parent_still_gets_an_edge(self, loader):
        """THE #273 BUG. Child in batch 1, parent in batch 2 — the edge must
        still exist once the load finishes."""
        loader.load_batch([ROOT, CHILD])      # parent 216851 does not exist yet
        loader.load_batch([PARENT])           # ...it arrives here
        loader.finalize()

        assert ("NCBITaxon:853", "NCBITaxon:216851") in loader._graph.edges, (
            "F. prausnitzii lost its parent edge because the genus was written "
            "in a later batch — the MATCH silently dropped the row (#273)"
        )

    def test_parent_first_ordering_still_works(self, loader):
        """The 99.5% that already worked must keep working."""
        loader.load_batch([ROOT, PARENT])
        loader.load_batch([CHILD])
        loader.finalize()
        assert ("NCBITaxon:853", "NCBITaxon:216851") in loader._graph.edges

    def test_every_taxon_with_a_parent_ends_up_linked(self, loader):
        """No orphans for any interleaving — the property #273 says is violated
        for 29% of the taxonomy."""
        loader.load_batch([CHILD])            # worst case: every child first
        loader.load_batch([PARENT])
        loader.load_batch([ROOT])
        loader.finalize()

        linked = {c for c, _ in loader._graph.edges}
        expected = {
            n["taxon_id"] for n in loader._graph.nodes.values()
            if n.get("parent_taxon_id")
        }
        assert linked == expected, f"unlinked taxa: {expected - linked}"

    def test_finalize_is_idempotent(self, loader):
        loader.load_batch([ROOT, PARENT, CHILD])
        loader.finalize()
        before = set(loader._graph.edges)
        loader.finalize()
        assert loader._graph.edges == before


class TestParentPointerIsPersisted:
    def test_node_write_stores_parent_taxon_id(self, loader):
        """Verified live: all 1,142,851 taxa have a null parent pointer, so the
        linkage cannot be re-derived in place. Persisting it is what makes the
        finalize pass — and any future repair — possible without re-reading
        nodes.dmp."""
        loader.load_batch([ROOT, PARENT, CHILD])
        assert loader._graph.nodes["NCBITaxon:853"].get("parent_taxon_id") == (
            "NCBITaxon:216851"
        )

    def test_root_has_no_parent_pointer(self, loader):
        """Only the NCBI root legitimately lacks a parent."""
        loader.load_batch([ROOT])
        assert loader._graph.nodes["NCBITaxon:1"].get("parent_taxon_id") is None
