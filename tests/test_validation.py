"""
Tests for cross-reference validation functions.
"""

import pytest
from unittest.mock import MagicMock

from database.validation import (
    CHECKS,
    EXPECTED_LABELS,
    validate_orphaned_nodes,
    validate_taxonomy_consistency,
    validate_metabolite_ids,
    validate_dangling_relationships,
    validate_source_coverage,
    run_cross_reference_validation,
)


def _mock_session_run(return_values):
    """
    Build a mock session whose .run() returns different results on successive calls.

    Each entry in return_values is either:
    - a list of dicts (for iteration / list(result))
    - a dict with a "single" key whose value is the dict returned by result.single()
    """
    session = MagicMock()
    call_results = []

    for rv in return_values:
        mock_result = MagicMock()
        if isinstance(rv, dict) and "single" in rv:
            mock_result.single.return_value = rv["single"]
            mock_result.__iter__ = MagicMock(return_value=iter([]))
            mock_result.__list__ = []
        else:
            # rv is a list of record dicts
            records = []
            for rec in rv:
                mock_rec = MagicMock()
                mock_rec.__getitem__ = lambda self, k, _r=rec: _r[k]
                mock_rec.items = lambda _r=rec: _r.items()
                records.append(mock_rec)
            mock_result.__iter__ = MagicMock(return_value=iter(records))
            mock_result.__list__ = records

            def _list(mr=mock_result, recs=records):
                return recs
            mock_result.__len__ = lambda self, recs=records: len(recs)
        call_results.append(mock_result)

    session.run = MagicMock(side_effect=call_results)
    return session


def _make_driver(session):
    """Wrap a mock session in a mock driver."""
    driver = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    driver.session.return_value = ctx
    return driver


class TestValidateOrphanedNodes:
    """Tests for validate_orphaned_nodes."""

    # Since #298 this is ONE label-agnostic query returning (label, cnt) rows,
    # not one query per EXPECTED_LABELS entry -- see
    # tests/test_validation_orphan_labels.py for why the per-label form went
    # blind to :Compound.

    def _driver_yielding(self, rows):
        session = MagicMock()
        result = MagicMock()
        records = []
        for rec in rows:
            mock_rec = MagicMock()
            mock_rec.__getitem__ = lambda self, k, _r=rec: _r[k]
            records.append(mock_rec)
        result.__iter__ = MagicMock(return_value=iter(records))
        session.run = MagicMock(return_value=result)
        return _make_driver(session)

    def test_no_orphans(self):
        """The database reports no orphaned nodes."""
        driver = self._driver_yielding([])

        result = validate_orphaned_nodes(driver, "testdb")

        assert result["passed"] is True
        assert result["warnings"] == 0
        assert result["errors"] == 0
        assert result["check"] == "Orphaned nodes (no relationships)"

    def test_with_orphans(self):
        """Some labels have orphaned nodes."""
        driver = self._driver_yielding([{"label": "Taxon", "cnt": 5}])

        result = validate_orphaned_nodes(driver, "testdb")

        assert result["passed"] is False
        assert result["warnings"] == 1
        assert result["details"]["orphaned_by_label"]["Taxon"] == 5
        assert result["details"]["total"] == 5


class TestValidateTaxonomyConsistency:
    """Tests for validate_taxonomy_consistency."""

    def test_consistent_taxonomy(self):
        """No duplicate NCBI IDs, no missing IDs."""
        session = _mock_session_run([
            [],  # No duplicate ncbi_tax_ids
            {"single": {"cnt": 0}},  # No missing ncbi_tax_id
        ])
        driver = _make_driver(session)

        result = validate_taxonomy_consistency(driver, "testdb")

        assert result["passed"] is True
        assert result["errors"] == 0
        assert result["warnings"] == 0

    def test_duplicate_ncbi_ids(self):
        """Duplicate NCBI tax IDs detected."""
        dup_record = MagicMock()
        dup_record.__getitem__ = lambda self, k: {
            "ncbi_id": "12345",
            "taxon_ids": ["NCBITaxon:12345", "NCBITaxon:12345_dup"],
        }[k]
        session = _mock_session_run([
            [dup_record],  # 1 duplicate
            {"single": {"cnt": 0}},
        ])
        driver = _make_driver(session)

        result = validate_taxonomy_consistency(driver, "testdb")

        assert result["passed"] is False
        assert result["errors"] == 1

    def test_missing_ncbi_ids(self):
        """Taxon nodes missing ncbi_tax_id trigger a warning."""
        session = _mock_session_run([
            [],  # No duplicates
            {"single": {"cnt": 3}},  # 3 missing
        ])
        driver = _make_driver(session)

        result = validate_taxonomy_consistency(driver, "testdb")

        assert result["passed"] is True  # warnings only, not errors
        assert result["warnings"] == 1


class TestValidateMetaboliteIds:
    """Tests for validate_metabolite_ids."""

    def test_all_ids_valid(self):
        """No metabolite ID issues."""
        session = _mock_session_run([
            {"single": {"cnt": 0}},  # no missing external IDs
            {"single": {"cnt": 0}},  # no bad HMDB
            {"single": {"cnt": 0}},  # no bad KEGG
            {"single": {"dup_count": 0}},  # no duplicate HMDB
        ])
        driver = _make_driver(session)

        result = validate_metabolite_ids(driver, "testdb")

        assert result["passed"] is True
        assert result["errors"] == 0
        assert result["warnings"] == 0

    def test_malformed_hmdb(self):
        """Malformed HMDB IDs cause an error."""
        session = _mock_session_run([
            {"single": {"cnt": 0}},
            {"single": {"cnt": 2}},  # 2 bad HMDB IDs
            {"single": {"cnt": 0}},
            {"single": {"dup_count": 0}},
        ])
        driver = _make_driver(session)

        result = validate_metabolite_ids(driver, "testdb")

        assert result["passed"] is False
        assert result["errors"] == 1

    def test_missing_external_ids_warning(self):
        """Metabolites with no external ID trigger a warning but still pass."""
        session = _mock_session_run([
            {"single": {"cnt": 5}},  # 5 missing external IDs
            {"single": {"cnt": 0}},
            {"single": {"cnt": 0}},
            {"single": {"dup_count": 0}},
        ])
        driver = _make_driver(session)

        result = validate_metabolite_ids(driver, "testdb")

        assert result["passed"] is True
        assert result["warnings"] == 1


class TestValidateDanglingRelationships:
    """Tests for validate_dangling_relationships."""

    def test_no_dangling(self):
        """All relationships have correct endpoint labels."""
        session = _mock_session_run([
            {"single": {"cnt": 0}},  # ASSOCIATED_WITH_DISEASE
            {"single": {"cnt": 0}},  # LINKED_TO_DISEASE
            {"single": {"cnt": 0}},  # PRODUCES
            {"single": {"cnt": 0}},  # PARTICIPATES_IN
            {"single": {"cnt": 0}},  # HAS_PARENT
        ])
        driver = _make_driver(session)

        result = validate_dangling_relationships(driver, "testdb")

        assert result["passed"] is True
        assert result["errors"] == 0

    def test_dangling_produces(self):
        """Dangling PRODUCES relationships are detected."""
        session = _mock_session_run([
            {"single": {"cnt": 0}},
            {"single": {"cnt": 0}},  # LINKED_TO_DISEASE
            {"single": {"cnt": 3}},  # 3 bad PRODUCES
            {"single": {"cnt": 0}},
            {"single": {"cnt": 0}},
        ])
        driver = _make_driver(session)

        result = validate_dangling_relationships(driver, "testdb")

        assert result["passed"] is False
        assert result["errors"] == 1


class TestValidateSourceCoverage:
    """Tests for validate_source_coverage."""

    def test_coverage_is_informational(self):
        """Source coverage always passes (informational check)."""
        # For each label: one UNION query for sources, one count query.
        # Derived from EXPECTED_LABELS, not hardcoded — a literal count is what
        # broke when the list was corrected in #298 (and the same smell #270
        # removed from the CHECKS-count assertions).
        n_labels = len(EXPECTED_LABELS)
        returns = []
        for _ in range(n_labels):
            # source query returns empty
            returns.append([])
        for _ in range(n_labels):
            # count query
            returns.append({"single": {"cnt": 0}})

        session = _mock_session_run(returns)
        driver = _make_driver(session)

        result = validate_source_coverage(driver, "testdb")

        assert result["passed"] is True
        assert isinstance(result["details"], dict)


class TestRunCrossReferenceValidation:
    """Tests for the aggregator function."""

    def test_returns_summary(self):
        """run_cross_reference_validation returns results and summary."""
        # Build a driver whose session always returns 0 counts
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.single.return_value = {"cnt": 0, "dup_count": 0}
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        session.run.return_value = mock_result

        driver = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=False)
        driver.session.return_value = ctx

        output = run_cross_reference_validation(driver, "testdb")

        assert "results" in output
        assert "summary" in output
        assert output["summary"]["total_checks"] == len(CHECKS)
        assert output["summary"]["passed"] + output["summary"]["failed"] == len(CHECKS)

    def test_handles_check_errors_gracefully(self):
        """If a check raises an exception, it is recorded as failed."""
        driver = MagicMock()
        driver.session.side_effect = Exception("connection lost")

        output = run_cross_reference_validation(driver, "testdb")

        assert output["summary"]["total_checks"] == len(CHECKS)
        assert output["summary"]["failed"] == len(CHECKS)
        assert output["summary"]["errors"] == len(CHECKS)


class TestResultFormat:
    """Verify all validation functions return the expected dict shape."""

    @pytest.mark.parametrize("fn", [
        validate_orphaned_nodes,
        validate_taxonomy_consistency,
        validate_metabolite_ids,
        validate_dangling_relationships,
        validate_source_coverage,
    ])
    def test_result_dict_keys(self, fn):
        """Each function returns a dict with the required keys."""
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.single.return_value = {"cnt": 0, "dup_count": 0}
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        session.run.return_value = mock_result

        driver = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=False)
        driver.session.return_value = ctx

        result = fn(driver, "testdb")

        assert "check" in result
        assert "passed" in result
        assert "details" in result
        assert "warnings" in result
        assert "errors" in result
        assert isinstance(result["passed"], bool)
        assert isinstance(result["warnings"], int)
        assert isinstance(result["errors"], int)
