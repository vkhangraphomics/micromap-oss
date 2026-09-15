"""#269 Step 2: backfill the shape of the 125 out-of-band Disbiome :Paper nodes.

The 2026-07-14 `graphomics-kg-audit` write created 125 :Paper nodes carrying
only `pmid`/`disbiome_sourced`/`title`/`year` — no `paper_id`, `source`, or
`created_at`, unlike loader-created papers. Step 1 (#269) stamped their
`organization_id`; this backfills the remaining shape so they match the
`pubmed_loader` form (`paper_id = "PMID:<pmid>"`, `source = "Disbiome"`).

No loader creates these nodes, so the repair cannot be re-run through `--pubmed`;
it is an explicit, idempotent backfill (mirrors `--backfill-scfa`).
"""
from unittest.mock import MagicMock

from database.ingestion.disbiome_loader import backfill_disbiome_paper_shape


def _driver_capturing(repaired=125):
    session = MagicMock()
    result = MagicMock()
    result.single.return_value = {"repaired": repaired}
    session.run.return_value = result
    driver = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    driver.session.return_value = ctx
    return driver, session


def test_returns_repaired_count():
    driver, _ = _driver_capturing(repaired=125)
    assert backfill_disbiome_paper_shape(driver, "micromap") == 125


def test_targets_only_disbiome_papers_missing_paper_id():
    driver, session = _driver_capturing()
    backfill_disbiome_paper_shape(driver, "micromap")
    cypher = session.run.call_args.args[0]
    assert "p.disbiome_sourced IS NOT NULL" in cypher
    assert "p.paper_id IS NULL" in cypher


def test_mints_pmid_paper_id_matching_loader_form():
    driver, session = _driver_capturing()
    backfill_disbiome_paper_shape(driver, "micromap")
    cypher = session.run.call_args.args[0]
    # pubmed_loader uses paper_id = "PMID:<pmid>"
    assert "'PMID:' + toString(p.pmid)" in cypher
    assert "p.source = coalesce(p.source, 'Disbiome')" in cypher
    assert "p.created_at = coalesce(p.created_at, datetime())" in cypher


def test_guards_against_creating_a_duplicate_paper_id():
    # If a PubMed :Paper already holds PMID:<pmid>, minting the same id on a
    # Disbiome twin would split identity (#267 class). Guard: skip when taken.
    driver, session = _driver_capturing()
    backfill_disbiome_paper_shape(driver, "micromap")
    cypher = session.run.call_args.args[0]
    assert "NOT EXISTS" in cypher and "other.paper_id = 'PMID:' + toString(p.pmid)" in cypher


def test_is_idempotent_shape_no_delete_no_overwrite():
    driver, session = _driver_capturing()
    backfill_disbiome_paper_shape(driver, "micromap")
    cypher = session.run.call_args.args[0]
    assert "DELETE" not in cypher            # additive repair, never destructive
    assert "coalesce(p.source" in cypher     # preserves an existing value
    assert "coalesce(p.organization_id" in cypher
