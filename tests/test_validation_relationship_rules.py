"""Tests for the taxonomy/relationship endpoint guardrails (#298).

Background — why these guardrails were vacuous:

`validate_dangling_relationships` checked endpoint labels for a relationship
type spelled `CHILD_OF`. Nothing has ever written a `CHILD_OF` edge: the only
writer of the taxonomy hierarchy is `ncbi_taxonomy_loader.py`, which MERGEs
`HAS_PARENT` (1,623,440 edges live — the largest relationship type in the KG).
`CHILD_OF` was absent from `db.relationshipTypes()` on kgdev entirely, so the
check matched zero rows and passed unconditionally, forever. Two integrity tests
in `test_kg_integrity.py` had the same defect.

The consequence: `HAS_PARENT` — two thirds of all relationships — had NO
endpoint validation, while a rule was spent on a type that does not exist. #273
(29% of taxa have no parent) went unreported by the checks nominally responsible
for taxonomy integrity.

This is the relationship-type twin of #269/#270 (hardcoded label list rots).
The fix mirrors #270: don't just correct the literal, add a guard that asks the
database whether each declared rule matches anything — so the NEXT rename is
reported instead of silently disarming a check.
"""
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from database.validation import (
    CHECKS,
    RELATIONSHIP_ENDPOINTS,
    validate_dangling_relationships,
    validate_relationship_rule_coverage,
)


_DB_DIR = Path(__file__).resolve().parent.parent / "database"

# `MERGE (a)-[r:TYPE]->(b)` / `CREATE (a)-[:TYPE {..}]->(b)` in loader Cypher.
_WRITE_PATTERN = re.compile(
    r"(?:MERGE|CREATE)\s*\([^)]*\)\s*-\s*\[\s*\w*\s*:\s*(\w+)", re.IGNORECASE
)


def _relationship_types_written_by_loaders():
    """Relationship types any module under `database/` actually writes.

    Scans source rather than the database so the answer does not depend on
    which sources happen to be loaded on the box running the tests.
    """
    written = set()
    for path in _DB_DIR.rglob("*.py"):
        if path.name == "validation.py":
            continue  # the declarations under test, not a writer
        written.update(_WRITE_PATTERN.findall(path.read_text(encoding="utf-8")))
    return written


def _endpoint_driver(bad_counts):
    """Driver for validate_dangling_relationships.

    `bad_counts` maps a relationship type -> number of badly-typed endpoints the
    database should report for it. Any type not listed reports 0.
    """
    session = MagicMock()

    def run(cypher, **_kwargs):
        result = MagicMock()
        cnt = 0
        for rel_type, bad in bad_counts.items():
            if f":{rel_type}]" in cypher:
                cnt = bad
                break
        result.single.return_value = {"cnt": cnt}
        return result

    session.run = MagicMock(side_effect=run)
    driver = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    driver.session.return_value = ctx
    return driver, session


def _coverage_driver(types_in_graph, counts=None):
    """Driver for validate_relationship_rule_coverage.

    `types_in_graph` is what `db.relationshipTypes()` reports; `counts` maps a
    type -> edge count (defaults to 1 for every type present, 0 otherwise).
    """
    counts = counts if counts is not None else {t: 1 for t in types_in_graph}
    session = MagicMock()

    def run(cypher, **_kwargs):
        result = MagicMock()
        if "db.relationshipTypes" in cypher:
            record = {"types": list(types_in_graph)}
            result.single.return_value = record
            return result
        for rel_type in list(counts) + list(types_in_graph):
            if f":{rel_type}]" in cypher:
                result.single.return_value = {"cnt": counts.get(rel_type, 0)}
                return result
        result.single.return_value = {"cnt": 0}
        return result

    session.run = MagicMock(side_effect=run)
    driver = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    driver.session.return_value = ctx
    return driver, session


class TestHasParentIsValidated:
    """The taxonomy hierarchy must actually be checked."""

    def test_bad_has_parent_endpoints_are_reported(self):
        """REGRESSION (#298): the defect, stated directly.

        A `HAS_PARENT` edge landing on a non-Taxon is exactly the corruption the
        taxonomy guardrail exists to catch. Against the old CHILD_OF-based check
        this passes clean, because `MATCH ()-[r:CHILD_OF]->()` matches nothing.
        """
        driver, _ = _endpoint_driver({"HAS_PARENT": 17})

        result = validate_dangling_relationships(driver, "testdb")

        assert result["passed"] is False
        assert "HAS_PARENT" in result["details"]
        assert "17" in result["details"]

    def test_child_of_is_never_queried(self):
        """Pins the mechanism: no query may name the phantom type.

        A future edit that reintroduces `CHILD_OF` — the literal that disarmed
        this check for the graph's largest relationship type — fails here.
        """
        driver, session = _endpoint_driver({})

        validate_dangling_relationships(driver, "testdb")

        for cypher in [call[0][0] for call in session.run.call_args_list]:
            assert "CHILD_OF" not in cypher, (
                "CHILD_OF does not exist in the graph; a rule spelled that way "
                "is vacuous (#298)"
            )

    def test_taxonomy_rule_expects_taxon_to_taxon(self):
        assert RELATIONSHIP_ENDPOINTS["HAS_PARENT"] == ("Taxon", "Taxon")
        assert "CHILD_OF" not in RELATIONSHIP_ENDPOINTS

    def test_clean_graph_passes(self):
        driver, _ = _endpoint_driver({})

        result = validate_dangling_relationships(driver, "testdb")

        assert result["passed"] is True
        assert result["errors"] == 0


class TestRelationshipRuleCoverage:
    """The rot guard: declared rules are cross-checked against reality."""

    def test_declared_rule_matching_nothing_is_reported(self):
        """REGRESSION (#298): the failure mode itself, not just the symptom.

        A rule naming a type with zero edges is a check that cannot fail --
        strictly worse than no check, because it reports green.

        Reported as a WARNING, not an error, and deliberately so: a partially
        loaded database (only `--taxonomy` run locally, say) legitimately has
        zero `LINKED_TO_DISEASE` edges, and failing there would fire on every
        dev box until someone learned to ignore the check -- the #270
        `:SchemaVersion` lesson. The hard, deterministic guard against a rule
        naming a type nothing writes is the static test below, which needs no
        database and cannot be confused by an unloaded source.
        """
        driver, _ = _coverage_driver(
            types_in_graph=["HAS_PARENT", "PRODUCES"],
            counts={"HAS_PARENT": 1623440, "PRODUCES": 218454, "CHILD_OF": 0},
        )

        result = validate_relationship_rule_coverage(
            driver, "testdb", rules={"CHILD_OF": ("Taxon", "Taxon")}
        )

        assert result["details"]["vacuous_rules"] == ["CHILD_OF"]
        assert result["warnings"] >= 1
        # Advisory, not fatal: an unloaded source must not fail `--validate`.
        assert result["errors"] == 0
        assert result["passed"] is True

    def test_unruled_relationship_type_in_graph_is_reported(self):
        """The other half of #298: 1.6M HAS_PARENT edges nobody validated."""
        driver, _ = _coverage_driver(
            types_in_graph=["HAS_PARENT", "PRODUCES"],
            counts={"HAS_PARENT": 1623440, "PRODUCES": 218454},
        )

        result = validate_relationship_rule_coverage(
            driver, "testdb", rules={"PRODUCES": ("Taxon", "Compound")}
        )

        assert result["details"]["unruled_types"] == ["HAS_PARENT"]
        assert result["warnings"] >= 1

    def test_empty_graph_does_not_cry_wolf(self):
        """A freshly-initialised database has no edges; every rule is trivially
        unmatched. Reporting that would fail `--validate` on every new DB — the
        `:SchemaVersion` lesson from #270. A check that cries wolf gets ignored.
        """
        driver, _ = _coverage_driver(types_in_graph=[], counts={})

        result = validate_relationship_rule_coverage(driver, "testdb")

        assert result["passed"] is True
        assert result["errors"] == 0

    def test_asks_the_database_which_types_exist(self):
        """Pins the mechanism (#270's lesson): derive from the DB, don't declare.

        A rewrite that compares two hardcoded lists reintroduces the rot this
        check exists to detect.
        """
        driver, session = _coverage_driver(types_in_graph=["HAS_PARENT"])

        validate_relationship_rule_coverage(driver, "testdb")

        cyphers = [call[0][0] for call in session.run.call_args_list]
        assert any("db.relationshipTypes" in c for c in cyphers), (
            "must ask the database which relationship types exist"
        )

    def test_shipped_rules_are_quiet_against_the_live_graph_shape(self):
        """The shipped RELATIONSHIP_ENDPOINTS, against kgdev's actual types."""
        live_types = [
            "ASSOCIATED_WITH_DISEASE", "HAS_PARENT", "PRODUCES",
            "PARTICIPATES_IN", "TARGETS", "MENTIONED_IN", "LINKED_TO_DISEASE",
            "IMPLICATED_IN", "ENCODED_BY", "EFFECTIVE_AGAINST",
        ]
        driver, _ = _coverage_driver(
            types_in_graph=live_types,
            counts={t: 1 for t in live_types},
        )

        result = validate_relationship_rule_coverage(driver, "testdb")

        assert "never fail" not in str(result["details"]), (
            f"a shipped rule names a type absent from the live graph: "
            f"{result['details']}"
        )

    def test_registered_in_cross_reference_validation(self):
        """Must actually run under --validate, not merely exist."""
        assert validate_relationship_rule_coverage in CHECKS


class TestNoPhantomRelationshipTypes:
    """The hard guard: a declared type that NOTHING writes.

    This is what would have caught #298 the day `CHILD_OF` was written, with no
    database involved. The runtime check above cannot distinguish "rule is dead"
    from "that source isn't loaded on this box"; source code can.
    """

    def test_every_declared_rule_names_a_type_some_loader_writes(self):
        writers = _relationship_types_written_by_loaders()
        phantoms = sorted(set(RELATIONSHIP_ENDPOINTS) - writers)

        assert not phantoms, (
            f"endpoint rule(s) name relationship types that no loader writes: "
            f"{phantoms}. Such a rule matches zero rows forever and reports "
            f"green -- the #298 defect. Point it at the type actually written."
        )

    def test_detector_catches_a_phantom(self):
        """REGRESSION: proves this guard is not itself vacuous.

        `CHILD_OF` is the real phantom from #298: absent from
        `db.relationshipTypes()` on kgdev and written by no loader, while the
        hierarchy ships as `HAS_PARENT`. If the detector cannot flag it, the
        test above would pass for the wrong reason.
        """
        writers = _relationship_types_written_by_loaders()

        assert "CHILD_OF" not in writers, (
            "detector claims CHILD_OF is written by a loader; it is not, and "
            "believing otherwise is exactly how #298 survived"
        )
        assert "HAS_PARENT" in writers, (
            "detector cannot see ncbi_taxonomy_loader's MERGE of HAS_PARENT, "
            "so it would call every real type a phantom"
        )


class TestResultShape:
    @pytest.mark.parametrize("check", [
        validate_dangling_relationships,
        validate_relationship_rule_coverage,
    ])
    def test_result_dict_shape(self, check):
        driver, _ = _coverage_driver(types_in_graph=["HAS_PARENT"])
        result = check(driver, "testdb")
        for key in ("check", "passed", "details", "warnings", "errors"):
            assert key in result
        assert isinstance(result["passed"], bool)
        assert isinstance(result["errors"], int)
