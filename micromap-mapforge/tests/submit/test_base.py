from micromap_mapforge.submit.base import DryRunReport, SubmissionReceipt


def test_dry_run_report_fields():
    r = DryRunReport(destination="micromap-core", would_write_nodes=10, would_write_relationships=5)
    assert r.destination == "micromap-core"
    assert r.would_write_nodes == 10


def test_submission_receipt_defaults():
    r = SubmissionReceipt(destination="micromap-core", success=True)
    assert r.nodes_written == 0
    assert r.relationships_written == 0
