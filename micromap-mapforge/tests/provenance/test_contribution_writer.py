from datetime import datetime, timezone
from unittest.mock import MagicMock

from micromap_mapforge.provenance.contribution import (
    ContributionRecord,
    write_contribution,
)


def _mock_driver() -> MagicMock:
    driver = MagicMock()
    session_ctx = MagicMock()
    session = MagicMock()
    driver.session.return_value = session_ctx
    session_ctx.__enter__ = MagicMock(return_value=session)
    session_ctx.__exit__ = MagicMock(return_value=False)

    captured_calls = []

    def execute_write(fn):
        tx = MagicMock()

        def _run(stmt, params=None):
            captured_calls.append((stmt, params))
            return MagicMock(consume=MagicMock(return_value=MagicMock(counters=MagicMock(nodes_created=1))))

        tx.run = _run
        return fn(tx)

    session.execute_write = execute_write
    driver._captured = captured_calls
    return driver


def _record():
    return ContributionRecord(
        contributor="acme", reviewer="alice", organization_id="acme",
        source_sha256="a1", mapping_sha256="b2",
        destination="micromap-core",
        submitted_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        resolved_count=5, unresolved_count=0, ambiguous_count=0,
    )


def test_write_contribution_executes_each_statement():
    driver = _mock_driver()
    write_contribution(driver, _record(), database="neo4j")
    # 5 parameterized statements: Org MERGE, Reviewer MERGE, Contribution MERGE+SET,
    # Org-CONTRIBUTED rel, Contribution-APPROVED_BY rel
    assert len(driver._captured) == 5


def test_write_contribution_opens_session_with_database():
    driver = _mock_driver()
    write_contribution(driver, _record(), database="graphomics")
    driver.session.assert_called_once_with(database="graphomics")


def test_write_contribution_passes_params_not_interpolated():
    """Values go into params, not into the query string."""
    driver = _mock_driver()
    write_contribution(driver, _record(), database="neo4j")
    for stmt, params in driver._captured:
        # No stringified value appears in the statement itself
        assert "'acme'" not in stmt
        assert "'alice'" not in stmt
        assert "'a1'" not in stmt
        assert "'b2'" not in stmt
        assert isinstance(params, dict)
