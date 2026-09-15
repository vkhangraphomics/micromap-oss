"""The orphaned-node check must not enumerate labels either (#298).

`validate_orphaned_nodes` looped over `EXPECTED_LABELS`, which still reads
`Metabolite` (0 nodes since the #146 rename) and has never listed `Compound`
(2,196 nodes live) or the derived `Gene` layer. So an orphaned `:Compound` --
exactly the shape #267 describes, where a split-identity metabolite ends up with
no edges at all -- was invisible to the check that exists to find orphans.

Identical rot to #269 (label list) and #298 (relationship-type list). #270 fixed
the null-org check by asking the database; the docstring it left behind flagged
`EXPECTED_LABELS` as still rotted. This closes that.
"""
from unittest.mock import MagicMock

from database.validation import validate_orphaned_nodes


def _driver_returning(rows):
    session = MagicMock()
    mock_result = MagicMock()
    records = []
    for rec in rows:
        mock_rec = MagicMock()
        mock_rec.__getitem__ = lambda self, k, _r=rec: _r[k]
        records.append(mock_rec)
    mock_result.__iter__ = MagicMock(return_value=iter(records))
    session.run = MagicMock(return_value=mock_result)

    driver = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    driver.session.return_value = ctx
    return driver, session


class TestOrphanedNodesIsLabelAgnostic:
    def test_reports_orphans_for_labels_absent_from_expected_labels(self):
        """REGRESSION (#298): `Compound` and `Gene` are in no hardcoded list."""
        driver, _ = _driver_returning([
            {"label": "Compound", "cnt": 12},
            {"label": "Gene", "cnt": 3},
        ])

        result = validate_orphaned_nodes(driver, "testdb")

        assert result["details"]["orphaned_by_label"]["Compound"] == 12
        assert result["details"]["orphaned_by_label"]["Gene"] == 3

    def test_does_not_enumerate_labels(self):
        """Pins the mechanism: one query, no `:Label` literals."""
        driver, session = _driver_returning([])

        validate_orphaned_nodes(driver, "testdb")

        assert session.run.call_count == 1, (
            "must be a single label-agnostic query; per-label enumeration is "
            "how the check went blind to :Compound"
        )
        cypher = session.run.call_args[0][0]
        for rotted in ("Metabolite", "Compound", "Gene", "Subject", "Sample"):
            assert f":{rotted}" not in cypher, (
                f"query hardcodes :{rotted}; it must ask the DB for labels"
            )

    def test_clean_graph_passes(self):
        driver, _ = _driver_returning([])

        result = validate_orphaned_nodes(driver, "testdb")

        assert result["passed"] is True
        assert result["warnings"] == 0

    def test_orphans_are_warnings_not_errors(self):
        """An orphan is suspicious, not proof of corruption -- a standalone
        reference node can legitimately have no edges yet."""
        driver, _ = _driver_returning([{"label": "Compound", "cnt": 12}])

        result = validate_orphaned_nodes(driver, "testdb")

        assert result["errors"] == 0
        assert result["warnings"] > 0
