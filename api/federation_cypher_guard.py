"""Allowlist validation over a bundle's emitted Cypher.

Defense in depth: the write target is already the tenant's own isolated
constituent, but this blocks procedure calls / CSV loads / DDL from riding in on
a hand-edited bundle. Statements are the emitted UNWIND/MERGE/MATCH shape.
"""
from __future__ import annotations

import re
from pathlib import Path

# NOTE: schema DDL (CREATE INDEX / CREATE CONSTRAINT / FOREACH) is intentionally
# NOT blocked here — `CREATE` is an allowed start keyword. This guard targets
# procedure calls / CSV loads / database-and-account DDL on hand-edited bundles,
# not schema management. Constituent DBs own their own index/constraint policy.
# Tightening this (or moving to a real Cypher parser) is a possible follow-up.
_ALLOWED_STARTS = ("UNWIND", "MERGE", "MATCH", "WITH", "CREATE", "SET", "RETURN", "OPTIONAL")
_FORBIDDEN = (
    re.compile(r"\bCALL\b", re.I),
    re.compile(r"\bLOAD\s+CSV\b", re.I),
    re.compile(r"\bapoc\.", re.I),
    re.compile(r"\bdbms\.", re.I),
    re.compile(r"\b(CREATE|DROP|ALTER)\s+(DATABASE|ALIAS|USER|ROLE)\b", re.I),
)


class DisallowedCypherError(Exception):
    """A bundle Cypher statement is outside the allowlist."""


def _split_statements(text: str) -> list[str]:
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("//")]
    cleaned = "\n".join(lines)
    return [s.strip() for s in cleaned.split(";") if s.strip()]


def validate_statement(stmt: str) -> None:
    for pat in _FORBIDDEN:
        if pat.search(stmt):
            raise DisallowedCypherError(f"forbidden token in statement: {stmt[:80]!r}")
    head = stmt.lstrip().split(None, 1)[0].upper() if stmt.strip() else ""
    if head not in _ALLOWED_STARTS:
        raise DisallowedCypherError(f"statement does not start with an allowed keyword: {stmt[:80]!r}")


def validate_bundle_cypher(bundle_dir: Path) -> None:
    cypher_dir = Path(bundle_dir) / "cypher"
    if not cypher_dir.is_dir():
        raise DisallowedCypherError("bundle has no cypher/ directory")
    for p in sorted(cypher_dir.iterdir()):
        if p.is_file() and p.suffix == ".cypher":
            for stmt in _split_statements(p.read_text(encoding="utf-8")):
                validate_statement(stmt)
