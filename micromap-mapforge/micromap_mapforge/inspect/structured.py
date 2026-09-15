"""Structured-string detection — D3 (#76 observation, #77).

Flags columns whose string values encode multiple fields in one cell so the
inspection report + the LLM mapper/proposer decompose them (one source column ->
several ontology fields) instead of treating them as opaque. The classic miss:
a GTDB/SILVA taxonomic lineage ``d__Bacteria;p__Pseudomonadota;...;s__`` mapped
as a single column rather than genus/species/phylum/rank.

Pure, no network, no LLM. Returns a hint dict (or None) attached to ColumnProfile.
"""
from __future__ import annotations

import re
from typing import Any

# Rank-letter -> rank name for `<letter>__name` lineage tokens (GTDB/SILVA/QIIME).
_RANK_NAMES = {
    "d": "domain", "k": "kingdom", "p": "phylum", "c": "class", "o": "order",
    "f": "family", "g": "genus", "s": "species", "t": "strain",
}
_RANK_TOKEN_RE = re.compile(r"^([a-z])__")

# Delimiters tried for the generic "consistent N-part" case, most specific first.
_DELIMITERS = (";", "|", "::")
_SAMPLE_CAP = 50


def detect_structured_string(values: list[Any], column_name: str | None = None) -> dict | None:
    """Classify a column's values as a structured string, or return None.

    Hints: ``{"kind": "rank_lineage", "delimiter": ";", "components": [...]}`` or
    ``{"kind": "delimited", "delimiter": "|", "parts": N}``.
    """
    vals = [str(v) for v in values[:_SAMPLE_CAP] if v is not None and str(v) != ""]
    if len(vals) < 2:
        return None
    return _rank_lineage(vals) or _delimited(vals)


def _rank_lineage(vals: list[str]) -> dict | None:
    # Each value is `;`-separated rank tokens, the majority shaped `<letter>__...`.
    ranks: list[str] = []
    for v in vals:
        tokens = [t.strip() for t in v.split(";") if t.strip()]
        if len(tokens) < 2:
            return None
        matched = [_RANK_TOKEN_RE.match(t) for t in tokens]
        if sum(m is not None for m in matched) < max(2, len(tokens) // 2):
            return None
        if not ranks:  # record the rank order from the first qualifying value
            ranks = [
                _RANK_NAMES.get(m.group(1), f"rank_{m.group(1)}")
                for m, _t in zip(matched, tokens, strict=True) if m is not None
            ]
    return {"kind": "rank_lineage", "delimiter": ";", "components": ranks}


def _delimited(vals: list[str]) -> dict | None:
    # A delimiter on which every value splits into the same count N > 1.
    for delim in _DELIMITERS:
        counts = {v.count(delim) for v in vals}
        if counts == {0}:  # delimiter absent everywhere
            continue
        if len(counts) == 1 and 0 not in counts:
            n = counts.pop() + 1
            if n > 1:
                return {"kind": "delimited", "delimiter": delim, "parts": n}
    return None
