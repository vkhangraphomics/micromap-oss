"""
Integration tests for knowledge graph data quality.

These tests validate schema, data integrity, cross-reference consistency,
and data completeness against a live Neo4j instance.

Run with:
    pytest tests/test_kg_integrity.py -v -m integration

Requires NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD environment variables
(defaults: bolt://localhost:7687, neo4j, password).
"""

import warnings
import re

import pytest

from tests.kg_invariants import INVARIANTS

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Expected schema elements
# ---------------------------------------------------------------------------

# Node labels a baseline-loaded graph must hold.
#
# Corrected in #298 alongside the relationship list, same rot:
#   Metabolite -> Compound  (renamed in #146; the Metabolite *token* still
#                            lingers in db.labels() with 0 nodes, so asserting
#                            the old name passed vacuously -- the label twin of
#                            the CHILD_OF defect)
#   Subject     removed     (no loader writes it)
#   Sample      removed     (real: metabo_lights + pride loaders write it, but
#                            those sources are not in the loaded baseline)
EXPECTED_NODE_LABELS = {
    "Taxon",
    "Disease",
    "Compound",
    "Pathway",
    "Gene",
    "Drug",
    "Protein",
    "Paper",
    "BodySite",
    "Study",
}

# Relationship types a baseline-loaded graph must hold.
#
# Corrected in #298 after checking each name against `db.relationshipTypes()`
# on kgdev and against what the loaders actually write:
#   CHILD_OF -> HAS_PARENT  (ncbi_taxonomy_loader MERGEs HAS_PARENT; nothing has
#                            ever written CHILD_OF, so the old entry made this
#                            test pass vacuously)
#   HAS_GENE  removed       (written by NO loader; still READ by api/routes
#                            genes.py + pathways.py, which therefore return
#                            empty forever -- tracked separately, not a schema
#                            expectation)
#   FOUND_IN  removed       (real: mbodymap_loader + hmdb_metabolomics_loader
#                            write it, but those sources are not part of the
#                            loaded baseline, so requiring it here fails on a
#                            correctly-loaded box)
EXPECTED_RELATIONSHIP_TYPES = {
    "ASSOCIATED_WITH_DISEASE",
    "PRODUCES",
    "PARTICIPATES_IN",
    "HAS_PARENT",
    "MENTIONED_IN",
    "TARGETS",
}

# Key indexes that should exist (subset; names may vary by loader version)
EXPECTED_INDEX_LABELS = {
    "Taxon",
    "Disease",
    "Metabolite",
    "Drug",
    "Pathway",
}

HMDB_PATTERN = re.compile(r"^HMDB\d{7}$")


# ===========================================================================
# Helpers
# ===========================================================================


def _single_value(session, query: str, key: str = None):
    """Run a query and return the single result value."""
    result = session.run(query)
    record = result.single()
    if record is None:
        return None
    if key is not None:
        return record[key]
    return record[0]


def _collect(session, query: str, key: str = None):
    """Run a query and return all values for a given key (or first column)."""
    result = session.run(query)
    if key is not None:
        return [record[key] for record in result]
    return [record[0] for record in result]


# ===========================================================================
# 1. Schema validation
# ===========================================================================


class TestSchemaValidation:
    """Verify that the expected schema elements are present in the database."""

    def test_expected_node_labels_exist(self, neo4j_session):
        """All expected node labels should be present in the database."""
        existing_labels = set(
            _collect(neo4j_session, "CALL db.labels() YIELD label RETURN label")
        )
        missing = EXPECTED_NODE_LABELS - existing_labels
        assert not missing, f"Missing node labels: {missing}"

    def test_expected_relationship_types_exist(self, neo4j_session):
        """All expected relationship types should be present in the database."""
        existing_types = set(
            _collect(
                neo4j_session,
                "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType",
            )
        )
        missing = EXPECTED_RELATIONSHIP_TYPES - existing_types
        assert not missing, f"Missing relationship types: {missing}"

    def test_key_indexes_exist(self, neo4j_session):
        """Key indexes should exist for the main node labels."""
        result = neo4j_session.run("SHOW INDEXES YIELD labelsOrTypes RETURN labelsOrTypes")
        indexed_labels = set()
        for record in result:
            labels_or_types = record["labelsOrTypes"]
            if labels_or_types:
                for label in labels_or_types:
                    indexed_labels.add(label)

        missing = EXPECTED_INDEX_LABELS - indexed_labels
        assert not missing, f"No indexes found for labels: {missing}"


# ===========================================================================
# 2. Data integrity
# ===========================================================================


class TestDataIntegrity:
    """Validate structural integrity of the knowledge graph data."""

    @pytest.mark.parametrize("invariant", INVARIANTS, ids=lambda i: i.name)
    def test_invariant_holds_on_the_live_graph(self, neo4j_session, invariant):
        """Every shared invariant, against whatever `NEO4J_URI` points at.

        The queries come from `tests/kg_invariants.py` and are also run against
        a seeded ephemeral Neo4j by `test_kg_invariants_seeded.py`, which proves
        each one both passes on valid data and fires on its own violation.

        That sharing is the point (#298). When this module owned its own copies,
        nothing established that they could fail — and three of them could not,
        having been written against `CHILD_OF`, a relationship type no loader
        writes. Add invariants there, not here.
        """
        violations = invariant.count(neo4j_session)

        assert violations == 0, (
            f"{invariant.name}: {violations} violation(s) on the live graph "
            f"({invariant.description})"
        )

    def test_taxonomy_hierarchy_is_not_empty(self, neo4j_session):
        """Guard the guards (#298).

        The two checks below are the only taxonomy-integrity assertions there
        are, and both are `MATCH`-and-count-zero: against a relationship type
        the graph does not hold they pass unconditionally. They spent years
        spelled `CHILD_OF`, which no loader has ever written, so they reported
        green while `HAS_PARENT` (~1.6M edges) went entirely unchecked. Assert
        the hierarchy exists first, so a rename disarms nothing silently.
        """
        count = _single_value(
            neo4j_session,
            "MATCH ()-[r:HAS_PARENT]->() RETURN count(r) AS edges",
            "edges",
        )
        assert count > 0, (
            "no HAS_PARENT edges: either the taxonomy is unloaded or the "
            "hierarchy was renamed, which would silently void the two "
            "integrity checks below (#298)"
        )


# ===========================================================================
# 3. Cross-reference consistency
# ===========================================================================


class TestCrossReferenceConsistency:
    """Validate cross-reference identifiers and uniqueness."""

    def test_no_duplicate_disease_names(self, neo4j_session):
        """Disease names should be unique (after lowercasing and trimming)."""
        duplicates = _collect(
            neo4j_session,
            """
            MATCH (d:Disease)
            WHERE d.name IS NOT NULL
            WITH toLower(trim(d.name)) AS norm_name, count(*) AS cnt
            WHERE cnt > 1
            RETURN norm_name
            """,
        )
        assert len(duplicates) == 0, (
            f"Found {len(duplicates)} duplicate disease name(s): "
            f"{duplicates[:10]}"
        )

    def test_hmdb_ids_follow_format(self, neo4j_session):
        """Metabolite HMDB IDs should match the HMDBXXXXXXX pattern."""
        bad_ids = _collect(
            neo4j_session,
            """
            MATCH (m:Compound)
            WHERE m.hmdb_id IS NOT NULL AND m.hmdb_id <> ''
            RETURN m.hmdb_id AS hmdb_id
            """,
        )
        invalid = [hid for hid in bad_ids if not HMDB_PATTERN.match(str(hid))]
        assert len(invalid) == 0, (
            f"Found {len(invalid)} Metabolite(s) with invalid HMDB ID format: "
            f"{invalid[:10]}"
        )

    def test_ncbi_tax_ids_are_unique(self, neo4j_session):
        """Taxon nodes with ncbi_tax_id should have unique values."""
        duplicates = _collect(
            neo4j_session,
            """
            MATCH (t:Taxon)
            WHERE t.ncbi_tax_id IS NOT NULL
            WITH t.ncbi_tax_id AS tax_id, count(*) AS cnt
            WHERE cnt > 1
            RETURN tax_id
            """,
        )
        assert len(duplicates) == 0, (
            f"Found {len(duplicates)} duplicate ncbi_tax_id value(s): "
            f"{duplicates[:10]}"
        )


# ===========================================================================
# 4. Data completeness (soft checks)
# ===========================================================================


class TestDataCompleteness:
    """
    Soft checks for minimal data presence.

    These issue warnings instead of failing, since the database may be
    partially loaded during development.
    """

    def _check_count(self, neo4j_session, label: str, min_count: int = 1):
        """Helper: warn if node count is below min_count."""
        count = _single_value(
            neo4j_session,
            f"MATCH (n:{label}) RETURN count(n) AS cnt",
            "cnt",
        )
        if count < min_count:
            warnings.warn(
                f"Expected at least {min_count} {label} node(s), found {count}",
                stacklevel=3,
            )
        return count

    def _check_rel_count(self, neo4j_session, rel_type: str, min_count: int = 1):
        """Helper: warn if relationship count is below min_count."""
        count = _single_value(
            neo4j_session,
            f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS cnt",
            "cnt",
        )
        if count < min_count:
            warnings.warn(
                f"Expected at least {min_count} {rel_type} relationship(s), found {count}",
                stacklevel=3,
            )
        return count

    def test_taxon_nodes_present(self, neo4j_session):
        """At least one Taxon node should exist."""
        self._check_count(neo4j_session, "Taxon")

    def test_disease_nodes_present(self, neo4j_session):
        """At least one Disease node should exist."""
        self._check_count(neo4j_session, "Disease")

    def test_metabolite_nodes_present(self, neo4j_session):
        """At least one Metabolite node should exist."""
        self._check_count(neo4j_session, "Metabolite")

    def test_associated_with_disease_relationships_present(self, neo4j_session):
        """At least one ASSOCIATED_WITH_DISEASE relationship should exist."""
        self._check_rel_count(neo4j_session, "ASSOCIATED_WITH_DISEASE")

    def test_produces_relationships_present(self, neo4j_session):
        """At least one PRODUCES relationship should exist."""
        self._check_rel_count(neo4j_session, "PRODUCES")
