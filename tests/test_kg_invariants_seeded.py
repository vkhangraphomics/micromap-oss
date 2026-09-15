"""Run the graph invariants against a seeded, ephemeral Neo4j (#298).

`test_kg_integrity.py` points at `NEO4J_URI` and skips when that box is
unreachable. No CI job supplies one: `repo-units` runs
`-m "not integration and not docker"`, and the only `-m integration` job runs
inside `micromap-mapforge/`. So those tests have not executed in CI at all —
including the three that #303 corrected after they spent years querying
`CHILD_OF`, a relationship type nothing writes, and passing unconditionally.

This module closes that. It spins up a throwaway Neo4j, and for every invariant
in `kg_invariants.INVARIANTS` asserts BOTH directions:

- against `CLEAN_GRAPH` the query reports zero — it does not fire on valid data;
- against its own `violation_setup` the query reports a violation — it *can*
  fail, which is the property #298 proved was missing.

The queries are imported, never re-typed. A copy here would prove the copy
works while the live suite kept running something else, which is the failure
mode this whole exercise is about.
"""
import pytest

from tests.kg_invariants import CLEAN_GRAPH, INVARIANTS, Invariant

# `integration` keeps it out of `repo-units` (which needs no Docker); `seeded`
# is what the repo-integration CI job selects. The distinction matters: most
# `-m integration` tests here point at an external `NEO4J_URI` and currently
# fail rather than skip when it is absent, so selecting the whole marker would
# land a permanently red job. `seeded` means "brings its own service".
pytestmark = [pytest.mark.integration, pytest.mark.seeded]


@pytest.fixture(scope="module")
def neo4j_container():
    """A throwaway Neo4j. Skips (never fails) when Docker is unavailable."""
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:  # pragma: no cover - env-dependent
        pytest.skip("testcontainers[neo4j] not installed")

    try:
        with Neo4jContainer("neo4j:5.26") as container:
            yield container
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"Could not start Neo4j container: {exc}")


@pytest.fixture
def session(neo4j_container):
    """A session over an empty database, reset between tests.

    Each test seeds exactly the graph it reasons about, so a violation left by
    one cannot silently satisfy — or break — the next.
    """
    driver = neo4j_container.get_driver()
    with driver.session() as sess:
        sess.run("MATCH (n) DETACH DELETE n")
        yield sess
        sess.run("MATCH (n) DETACH DELETE n")


def _seed(session, cypher: str) -> None:
    for statement in filter(None, (s.strip() for s in cypher.split("\n\n"))):
        session.run(statement)


@pytest.mark.parametrize("invariant", INVARIANTS, ids=lambda i: i.name)
def test_invariant_holds_on_a_valid_graph(session, invariant: Invariant):
    """No invariant may fire on data that is correct."""
    _seed(session, CLEAN_GRAPH)

    violations = invariant.count(session)

    assert violations == 0, (
        f"{invariant.name} reported {violations} violation(s) on the clean "
        f"fixture — the check fires on valid data, so every real run is noise. "
        f"({invariant.description})"
    )


@pytest.mark.parametrize("invariant", INVARIANTS, ids=lambda i: i.name)
def test_invariant_catches_its_own_violation(session, invariant: Invariant):
    """REGRESSION (#298): a check that cannot fail is not a check.

    Three taxonomy guardrails passed for years against `CHILD_OF`, which no
    loader writes, so they matched nothing and reported green. Nothing noticed,
    because nobody had ever seen them fail. This asserts each query fires on the
    corruption it exists to detect.
    """
    _seed(session, invariant.violation_setup)

    violations = invariant.count(session)

    assert violations > 0, (
        f"{invariant.name} reported 0 violations against data built to violate "
        f"it — the query cannot fail and is therefore vacuous, the exact #298 "
        f"defect. ({invariant.description})"
    )


class TestTheSuiteCannotGoVacuous:
    """Guards on the guards themselves."""

    def test_every_invariant_has_a_violation_case(self):
        """An invariant with no way to break it can never be shown to work."""
        missing = [i.name for i in INVARIANTS if not i.violation_setup.strip()]

        assert not missing, (
            f"invariant(s) {missing} have no violation_setup, so nothing proves "
            f"they can fail — add one rather than trusting the query by eye"
        )

    def test_every_invariant_query_returns_the_expected_column(self):
        for invariant in INVARIANTS:
            assert "AS violations" in invariant.cypher, (
                f"{invariant.name} must return a `violations` column; "
                f"`Invariant.count` reads that name"
            )

    def test_invariants_are_not_empty(self):
        """A zero-length INVARIANTS tuple would make both suites above pass."""
        assert len(INVARIANTS) >= 8
