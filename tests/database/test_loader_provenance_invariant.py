"""Static check: every loader that sets `n.source = 'X'` on a NODE must also
set `n.sources = ['X']` so /api/v1/provenance/sources can count the node.

Regression for issue #83: ChEMBL, PubChem, CARD, neurological, and produces
loaders historically set only the scalar `n.source`, making ~19k production
nodes invisible to the endpoint (which counted by `n.sources`). This test
locks in the both-shapes contract so a future loader can't regress it.

The check intentionally only inspects NODE properties (anything matching
`<single-letter or word>.source =`); relationships are correctly queried by
the scalar `r.source` and don't need the list. We distinguish by looking at
which variable name appears on the left of the property assignment — common
node variables in this codebase are `n`, `d`, `dc`, `drug`, `protein`,
`compound`, `assay`, `m`, `t`, `s`.
"""

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INGESTION_DIR = REPO_ROOT / "database" / "ingestion"

# Variable names used for NODES in this codebase. Anything not in this list
# is treated as a relationship variable (and exempted from the rule).
NODE_VAR_NAMES = frozenset({
    "n", "d", "dc", "drug", "protein", "compound", "assay",
    "m", "t", "s", "p", "g", "pa", "node",
    "disease", "taxon", "metabolite", "pathway", "gene", "paper",
})


# Loader file -> set of (variable, source_value) pairs we know are nodes and
# therefore must also set `sources`. Computed at module-import time.
_SOURCE_ASSIGN_RE = re.compile(
    r"(?P<var>\b\w+)\.source\s*=\s*['\"](?P<src>[^'\"]+)['\"]"
)
_SOURCES_ASSIGN_RE = re.compile(
    r"(?P<var>\b\w+)\.sources\s*=\s*\[\s*['\"](?P<src>[^'\"]+)['\"]"
)


def _line_for(text: str, idx: int) -> str:
    """Return the line of `text` containing offset `idx`."""
    start = text.rfind("\n", 0, idx) + 1
    end = text.find("\n", idx)
    return text[start:end if end != -1 else None]


def _is_read_only_clause(line: str) -> bool:
    """Cypher read-only clauses don't assign — `WHERE x.source = 'y'`,
    `WITH x WHERE …`, etc. Skip them when looking for assignments."""
    stripped = line.lstrip()
    return (
        stripped.startswith("WHERE ")
        or stripped.startswith("AND ")
        or stripped.startswith("OR ")
        or "WHERE " in stripped[:stripped.find(".source") if ".source" in stripped else 0]
    )


def _scan(loader_path: Path) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """Return (scalar_assigns, list_assigns) as sets of (var, source_value)."""
    text = loader_path.read_text(encoding="utf-8")
    scalars = {
        (m.group("var"), m.group("src"))
        for m in _SOURCE_ASSIGN_RE.finditer(text)
        if m.group("var") in NODE_VAR_NAMES
        and not _is_read_only_clause(_line_for(text, m.start()))
    }
    lists = {
        (m.group("var"), m.group("src"))
        for m in _SOURCES_ASSIGN_RE.finditer(text)
        if m.group("var") in NODE_VAR_NAMES
    }
    return scalars, lists


def test_every_node_scalar_source_has_matching_sources_list():
    """For every loader file, every NODE-variable `.source = 'X'` assignment
    must have a matching `.sources = ['X']` for the same variable."""
    offenders: list[str] = []
    for loader_path in sorted(INGESTION_DIR.glob("*_loader.py")):
        scalars, lists = _scan(loader_path)
        missing = scalars - lists
        for var, src in sorted(missing):
            offenders.append(
                f"  {loader_path.name}: `{var}.source = '{src}'` has no "
                f"matching `{var}.sources = ['{src}']`"
            )
    assert not offenders, (
        "Issue #83 regression: a loader sets the scalar `node.source` but not "
        "the `node.sources` list. /api/v1/provenance/sources counts by "
        "`n.sources` and would silently miss these nodes:\n"
        + "\n".join(offenders)
    )


def test_backfill_provenance_script_has_sync_from_scalar_pass():
    """Issue #83: the backfill script must have a 'sync sources list from
    scalar source' pass to repair the historical state on existing
    deployments. The pass condition is the inverse of the original guard."""
    backfill = (REPO_ROOT / "database" / "backfill_provenance.py").read_text(
        encoding="utf-8"
    )
    assert "n.source IS NOT NULL AND (n.sources IS NULL OR size(n.sources) = 0)" in backfill, (
        "backfill_provenance.py is missing the sync-from-scalar pass needed "
        "to repair production graphs after issue #83."
    )
    assert "n.sources = [n.source]" in backfill
