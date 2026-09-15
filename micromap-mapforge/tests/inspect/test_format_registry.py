"""#286 G7: the source-format registry is open (was a closed Literal).

A new inspector registers its format via register_format() instead of editing a
Literal in types.py — the same closed-enum coupling #202 retired elsewhere.
"""
from micromap_mapforge.inspect.types import (
    KNOWN_FORMATS, register_format, SourceProfile,
)


def test_builtin_formats_are_registered():
    for f in ("csv", "tsv", "json", "jsonl", "parquet", "sql_dump", "xlsx", "pdf"):
        assert f in KNOWN_FORMATS


def test_register_format_is_additive_and_idempotent():
    assert register_format("vcf") == "vcf"
    assert "vcf" in KNOWN_FORMATS
    register_format("vcf")  # idempotent — no duplicate/raise
    assert sum(1 for x in KNOWN_FORMATS if x == "vcf") == 1


def test_source_profile_accepts_a_format_never_enumerated_in_a_literal():
    # The whole point of G7: a profile can carry a format string the base set
    # never listed, with no type/enum change.
    p = SourceProfile(path="a.h5ad", format="anndata", row_count_estimate=0, columns=[])
    assert p.format == "anndata"
    assert p.to_dict()["format"] == "anndata"
