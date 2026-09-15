"""Tests for the null-`organization_id` guardrail (#269).

Background — why this check is deliberately label-agnostic:

Two guardrails already existed and BOTH missed the 139 org-less nodes that #269
found on kgdev, for the same reason: they enumerate a hardcoded label list that
rotted out from under them.

- `tests/test_no_null_org_nodes.py` (#200) checks `Metabolite`, which has had 0
  nodes since the #146 `:Metabolite` -> `:Compound` migration. It would have
  sailed past the 12 org-less `:Compound` nodes, and lists no `Gene` at all
  (the GPV-434 bug).
- `database/validation.EXPECTED_LABELS` has the same rot (`Metabolite`, no
  `Compound`).

So the check MUST NOT enumerate labels. It asks the database which labels exist,
which is why the tests below pin single-query / label-agnostic behaviour rather
than just "returns a number".
"""
import pytest
from unittest.mock import MagicMock, patch

from database.validation import (
    CHECKS,
    validate_null_organization_id,
)


def _driver_returning(rows):
    """Mock driver whose single session.run() yields `rows` (list of dicts)."""
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


class TestValidateNullOrganizationId:
    def test_passes_when_no_orgless_nodes(self):
        driver, _ = _driver_returning([])

        result = validate_null_organization_id(driver, "testdb")

        assert result["passed"] is True
        assert result["errors"] == 0
        assert "No nodes" in str(result["details"])

    def test_fails_and_reports_offenders_by_label(self):
        """The exact #269 shape: 125 Paper + 12 Compound + 2 Protein = 139."""
        driver, _ = _driver_returning([
            {"label": "Paper", "cnt": 125},
            {"label": "Compound", "cnt": 12},
            {"label": "Protein", "cnt": 2},
        ])

        result = validate_null_organization_id(driver, "testdb")

        assert result["passed"] is False
        # ERROR, not warning: these nodes are invisible to every API caller.
        assert result["errors"] == 139
        assert result["warnings"] == 0
        assert result["details"]["orgless_by_label"] == {
            "Paper": 125, "Compound": 12, "Protein": 2,
        }
        assert result["details"]["total"] == 139

    def test_catches_labels_absent_from_every_hardcoded_list(self):
        """REGRESSION (#269): the defect that let this bug through.

        `Compound` and `Gene` appear in NEITHER `EXPECTED_LABELS` nor the #200
        test's LABELS. A label-enumerating check reports 0 for them forever.
        This asserts we catch them purely because the DB reported them.
        """
        driver, _ = _driver_returning([
            {"label": "Compound", "cnt": 12},
            {"label": "Gene", "cnt": 545},
        ])

        result = validate_null_organization_id(driver, "testdb")

        assert result["passed"] is False
        assert result["details"]["orgless_by_label"]["Compound"] == 12
        assert result["details"]["orgless_by_label"]["Gene"] == 545

    def test_does_not_enumerate_labels(self):
        """REGRESSION (#269): one label-agnostic query, not one-per-label.

        Pins the mechanism, not just the output — a future refactor that
        reintroduces a hardcoded label loop fails here.
        """
        driver, session = _driver_returning([])

        validate_null_organization_id(driver, "testdb")

        assert session.run.call_count == 1, (
            "must be a single label-agnostic query; per-label enumeration is "
            "the exact defect that let #269 through"
        )
        cypher = session.run.call_args[0][0]
        for rotted in ("Metabolite", "Compound", "Gene", "Paper", "Taxon"):
            assert f":{rotted}" not in cypher, (
                f"query hardcodes :{rotted}; it must ask the DB for labels"
            )
        assert "organization_id IS NULL" in cypher

    def test_registered_in_cross_reference_validation(self):
        """The check must actually run as part of --validate, not just exist."""
        assert validate_null_organization_id in CHECKS, (
            "validate_null_organization_id is not wired into CHECKS, so "
            "--validate would never run it"
        )

    def test_runs_first_so_invisibility_is_reported_before_downstream_noise(self):
        """Org-less nodes explain other checks' odd counts — report them first."""
        assert CHECKS[0] is validate_null_organization_id

    def test_internal_bookkeeping_labels_are_excluded(self):
        """`:SchemaVersion` is schema metadata, not tenant data.

        `neo4j_schema.py` MERGEs it with no `organization_id` and the API never
        serves it, so flagging it would be a false positive on every fresh DB —
        and a check that cries wolf is a check people learn to ignore.
        """
        driver, session = _driver_returning([])

        validate_null_organization_id(driver, "testdb")

        passed_labels = session.run.call_args.kwargs["internal_labels"]
        assert "SchemaVersion" in passed_labels, (
            "internal labels must be excluded, or --validate fails on a freshly "
            "initialised database"
        )

    def test_exclusion_is_fail_safe_not_a_data_label_allowlist(self):
        """Only *internal* labels are skipped; unknown data labels still fail.

        The exclusion must stay small and negative. An inclusion list is what
        rotted in #269 — a new data label must be caught by default.
        """
        from database.validation import INTERNAL_LABELS

        assert INTERNAL_LABELS == frozenset({"SchemaVersion"})
        for data_label in ("Compound", "Gene", "Paper", "Taxon", "Decision"):
            assert data_label not in INTERNAL_LABELS

        driver, _ = _driver_returning([{"label": "SomeBrandNewLabel", "cnt": 4}])
        result = validate_null_organization_id(driver, "testdb")
        assert result["passed"] is False
        assert result["details"]["orgless_by_label"]["SomeBrandNewLabel"] == 4

    def test_result_dict_shape(self):
        driver, _ = _driver_returning([])
        result = validate_null_organization_id(driver, "testdb")
        for key in ("check", "passed", "details", "warnings", "errors"):
            assert key in result
        assert isinstance(result["passed"], bool)
        assert isinstance(result["errors"], int)


class TestValidateCliExitCode:
    """`--validate` must FAIL, not just print (#269).

    Before this, validate_knowledge_graph printed "SOME CHECKS FAILED" and the
    CLI still exited 0 — so a broken graph looked identical to a clean one to
    any script, deploy gate or operator skimming the exit status.
    """

    def _args(self, *argv):
        from database.load_knowledge_graph import _build_arg_parser
        return _build_arg_parser().parse_args(list(argv))

    def _summary(self, errors):
        return {
            "results": [],
            "summary": {
                "total_checks": 1, "passed": 0 if errors else 1,
                "failed": 1 if errors else 0, "warnings": 0, "errors": errors,
            },
        }

    def test_validate_exits_nonzero_when_errors(self):
        from database.load_knowledge_graph import _dispatch

        args = self._args("--validate", "--neo4j-database", "neo4j")
        base = "database.load_knowledge_graph."
        with patch(base + "validate_knowledge_graph"), \
             patch("database.validation.run_cross_reference_validation",
                   return_value=self._summary(139)):
            with pytest.raises(SystemExit) as exc:
                _dispatch(args, MagicMock(), s3_manager=None)
        assert exc.value.code == 1

    def test_validate_exits_zero_when_clean(self):
        from database.load_knowledge_graph import _dispatch

        args = self._args("--validate", "--neo4j-database", "neo4j")
        base = "database.load_knowledge_graph."
        with patch(base + "validate_knowledge_graph"), \
             patch("database.validation.run_cross_reference_validation",
                   return_value=self._summary(0)):
            _dispatch(args, MagicMock(), s3_manager=None)  # must not raise

    def test_all_does_not_enter_the_failing_exit_branch(self):
        """`--all` stays advisory — a long load must not abort at the report step.

        Asserted structurally: the exit lives behind `args.validate or
        args.validate_xrefs`, and `--all` sets neither. Driving `_dispatch`
        with `--all` would run the real loaders (network/S3), so this pins the
        flag wiring instead.
        """
        args = self._args("--all", "--neo4j-database", "neo4j")
        assert args.all is True
        assert args.validate is False
        assert args.validate_xrefs is False
