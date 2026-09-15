"""Backfill for stale is_scfa flags left by the #277 substring bug.

Re-running `--hmdb` with the fixed filter does NOT repair existing data: the
1,169 false-positive compounds (Fenvalerate, Ethyl acetate, Starch acetate…)
are now *excluded* by the filter, so the loader never visits them and their
`is_scfa = true` / `carbon_chain_length` stay stale forever. MERGE/SET is
additive; nothing clears a flag that stops being written.

Hence an explicit backfill.
"""
from unittest.mock import MagicMock, patch

from database.ingestion.hmdb_loader import backfill_scfa_flags, SCFA_NAMES


def _driver_capturing():
    session = MagicMock()
    result = MagicMock()
    result.single.return_value = {"cleared": 1169}
    session.run.return_value = result
    driver = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    driver.session.return_value = ctx
    return driver, session


def test_returns_cleared_count():
    driver, _ = _driver_capturing()
    assert backfill_scfa_flags(driver, "micromap") == 1169


def test_targets_only_stale_true_flags():
    driver, session = _driver_capturing()
    backfill_scfa_flags(driver, "micromap")
    cypher = session.run.call_args[0][0]
    assert "c.is_scfa = true" in cypher, "must only touch nodes flagged true"
    assert "SET c.is_scfa = false" in cypher
    assert "c.carbon_chain_length = null" in cypher, (
        "chain length came from the same substring match and is equally wrong"
    )


def test_spares_the_real_scfas():
    """The genuine SCFAs must keep their flag — this is a repair, not a purge."""
    driver, session = _driver_capturing()
    backfill_scfa_flags(driver, "micromap")
    passed = session.run.call_args.kwargs["scfa_names"]
    for real in ("butyric acid", "acetic acid", "propionic acid", "valeric acid"):
        assert real in passed
    # exact list, straight from the module — no second copy to rot
    assert set(passed) == set(SCFA_NAMES)


def test_matches_on_name_and_iupac_name():
    """Existing nodes have no synonyms yet, so name/IUPAC are all we can use."""
    driver, session = _driver_capturing()
    backfill_scfa_flags(driver, "micromap")
    cypher = session.run.call_args[0][0]
    assert "toLower(c.name)" in cypher
    assert "iupac_name" in cypher


def test_runs_against_the_named_database():
    driver, _ = _driver_capturing()
    backfill_scfa_flags(driver, "micromap")
    driver.session.assert_called_once_with(database="micromap")


class TestCliWiring:
    def _args(self, *argv):
        from database.load_knowledge_graph import _build_arg_parser
        return _build_arg_parser().parse_args(list(argv))

    def test_flag_registered(self):
        assert self._args("--backfill-scfa", "--neo4j-database", "neo4j").backfill_scfa is True

    def test_flag_runs_backfill_only(self):
        from database.load_knowledge_graph import _dispatch

        args = self._args("--backfill-scfa", "--neo4j-database", "neo4j")
        driver = MagicMock()
        with patch("database.load_knowledge_graph.backfill_scfa_flags",
                   return_value=7) as p:
            _dispatch(args, driver, s3_manager=None)
        p.assert_called_once_with(driver, "neo4j")

    def test_is_standalone_not_part_of_all(self):
        """Like --derive: a repair step, deliberately not swept into --all."""
        args = self._args("--all", "--neo4j-database", "neo4j")
        assert args.backfill_scfa is False
