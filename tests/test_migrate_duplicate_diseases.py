"""#267: merge duplicate :Disease node pairs onto one canonical node.

The collapse/planning logic is pure Python (no Neo4j); the actual APOC
mergeNodes is exercised in CI's seeded run and validated live.
"""
from unittest.mock import MagicMock

import pytest

from database.migrate_duplicate_diseases import (
    collapse_key,
    _plan_from_rows,
    migrate,
    _MERGE,
)


# --- collapse_key ---------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("crohn disease", "crohns disease"),
    ("parkinson disease", "parkinsons disease"),
    ("alzheimer disease", "alzheimers disease"),
    ("kidney disease", "kidney diseases"),
    ("inflammatory bowel disease", "inflammatory bowel diseases"),
    ("crohns disease", "crohn's disease"),
])
def test_collapse_key_pairs_variants(a, b):
    assert collapse_key(a) == collapse_key(b)


def test_collapse_key_keeps_genuinely_distinct_diseases_apart():
    # 'parkinsonian syndrome' is a different disease, not a parkinson variant.
    assert collapse_key("parkinsonian syndrome") != collapse_key("parkinson disease")
    assert collapse_key("ulcerative colitis") != collapse_key("crohn disease")


def test_collapse_key_does_not_strip_short_words():
    # a 3-char word ending in s must survive intact (guard against over-merge).
    assert collapse_key("ibs") == "ibs"


# --- _plan_from_rows ------------------------------------------------------

def _row(norm, name, eid, taxa):
    return {"norm": norm, "name": name, "id": f"disease:{eid}", "eid": eid, "taxa": taxa}


def test_plan_picks_genera_richest_as_canonical():
    rows = [
        _row("crohn disease", "Crohn Disease", "e1", 1356),
        _row("crohns disease", "Crohn's Disease", "e2", 243),
    ]
    plan = _plan_from_rows(rows)
    assert len(plan) == 1
    p = plan[0]
    assert p["canonical_norm"] == "crohn disease"       # 1356 > 243
    assert [d["norm"] for d in p["duplicates"]] == ["crohns disease"]


def test_plan_skips_singletons():
    rows = [
        _row("ulcerative colitis", "Ulcerative Colitis", "e1", 500),
        _row("parkinsonian syndrome", "Parkinsonian syndrome", "e2", 0),
    ]
    assert _plan_from_rows(rows) == []


def test_plan_handles_multiple_groups_and_is_deterministic():
    rows = [
        _row("parkinsons disease", "Parkinson's Disease", "p2", 4),
        _row("parkinson disease", "Parkinson Disease", "p1", 1123),
        _row("crohns disease", "Crohn's Disease", "c2", 243),
        _row("crohn disease", "Crohn Disease", "c1", 1356),
    ]
    plan = _plan_from_rows(rows)
    assert [p["canonical_norm"] for p in plan] == ["crohn disease", "parkinson disease"]
    assert all(len(p["duplicates"]) == 1 for p in plan)


# --- migrate() ------------------------------------------------------------

def _driver_returning(rows):
    session = MagicMock()

    def run(cypher, **params):
        result = MagicMock()
        if "MATCH (d:Disease)" in cypher and "count(DISTINCT t)" in cypher:
            result.__iter__ = lambda self, _r=rows: iter(_r)
        else:  # the _MERGE call
            result.single.return_value = {"survivor": params.get("canon_eid")}
        return result

    session.run = MagicMock(side_effect=run)
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    driver = MagicMock()
    driver.session.return_value = ctx
    return driver, session


def test_migrate_dry_run_reports_without_writing():
    rows = [
        _row("crohn disease", "Crohn Disease", "c1", 1356),
        _row("crohns disease", "Crohn's Disease", "c2", 243),
    ]
    driver, session = _driver_returning(rows)
    report = migrate(driver, "micromap", dry_run=True)
    assert report["dry_run"] is True
    assert report["planned"][0]["canonical_norm"] == "crohn disease"
    # no merge issued in a dry run
    assert not any("apoc.refactor.mergeNodes" in c.args[0] for c in session.run.call_args_list)


def test_migrate_apply_merges_and_preserves_aliases():
    rows = [
        _row("crohn disease", "Crohn Disease", "c1", 1356),
        _row("crohns disease", "Crohn's Disease", "c2", 243),
    ]
    driver, session = _driver_returning(rows)
    report = migrate(driver, "micromap", dry_run=False)
    assert report["dry_run"] is False
    assert len(report["merged"]) == 1
    m = report["merged"][0]
    assert m["canonical_norm"] == "crohn disease"
    assert m["folded"] == ["crohns disease"]
    # both the folded surface name and its normalized value are kept as aliases
    assert "Crohn's Disease" in m["aliases"] and "crohns disease" in m["aliases"]
    merge_call = next(c for c in session.run.call_args_list
                      if "apoc.refactor.mergeNodes" in c.args[0])
    assert merge_call.kwargs["canon_eid"] == "c1"
    assert merge_call.kwargs["dup_eids"] == ["c2"]


def test_merge_cypher_dedupes_relationships():
    # mergeRels:true is what collapses the taxa the twins share into one edge
    # rather than leaving a duplicate ASSOCIATED_WITH_DISEASE.
    assert "mergeRels: true" in _MERGE
    assert "properties: 'discard'" in _MERGE
