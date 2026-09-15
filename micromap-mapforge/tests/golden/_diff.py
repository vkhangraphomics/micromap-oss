"""Human-readable golden-fixture diffs (3b-4 / #128).

When a golden test fails, dumping the full generated + expected file is
unreadable for fixtures larger than ~20 lines. This helper produces a
context-aware diff per content type so reviewers see "this property added"
or "this batch shrunk from 3 to 2" instead of a wall of YAML.

Dispatch by file extension:
  - .cypher → unified text diff with 3 lines of context (preserves emit
    semantics — order of UNWIND blocks, MATCH vs MERGE shape, etc.)
  - .yaml   → structural diff on parsed nested dicts/lists; falls back to
    text diff if YAML parsing fails on either side.
  - .json   → structural diff on parsed JSON; falls back to text diff.
  - other   → unified text diff.

Used by ``tests/test_m2_golden.py`` and friends via ``diff_file_pair()``.
Importable as ``from tests.golden._diff import diff_file_pair`` once the
``tests/`` package is on the import path (which it is — pytest auto-adds it).
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

import yaml


def diff_text(generated: str, expected: str, *, context: int = 3,
              from_label: str = "generated", to_label: str = "expected") -> str:
    """Unified text diff with `context` lines of surrounding context. Empty
    string if the two are equal."""
    if generated == expected:
        return ""
    lines = difflib.unified_diff(
        expected.splitlines(keepends=True),
        generated.splitlines(keepends=True),
        fromfile=to_label,
        tofile=from_label,
        n=context,
    )
    return "".join(lines)


def diff_structured(generated: Any, expected: Any, *, path: str = "") -> list[str]:
    """Recursive structural diff on parsed JSON/YAML values. Returns one
    string per difference, keyed by dotted path. Lists are compared by index;
    dicts by key. Skips when both are equal."""
    if generated == expected:
        return []

    out: list[str] = []
    if isinstance(generated, dict) and isinstance(expected, dict):
        for k in sorted(set(generated) | set(expected)):
            sub_path = f"{path}.{k}" if path else k
            if k not in expected:
                out.append(f"+ {sub_path} = {generated[k]!r}")
            elif k not in generated:
                out.append(f"- {sub_path} (was {expected[k]!r})")
            else:
                out.extend(diff_structured(generated[k], expected[k], path=sub_path))
        return out

    if isinstance(generated, list) and isinstance(expected, list):
        if len(generated) != len(expected):
            out.append(f"~ {path or '<root>'}: length {len(expected)} → {len(generated)}")
        # Length mismatches are reported above and below; this zip covers only
        # the overlapping prefix (strict=False).
        for i, (g, e) in enumerate(zip(generated, expected, strict=False)):
            out.extend(diff_structured(g, e, path=f"{path}[{i}]"))
        for i in range(len(expected), len(generated)):
            out.append(f"+ {path}[{i}] = {generated[i]!r}")
        for i in range(len(generated), len(expected)):
            out.append(f"- {path}[{i}] (was {expected[i]!r})")
        return out

    return [f"~ {path or '<root>'}: {expected!r} → {generated!r}"]


def diff_yaml(generated: str, expected: str) -> str:
    """Parse both as YAML and structural-diff. Falls back to text diff if
    either side fails to parse (a malformed YAML golden is itself a finding
    worth surfacing in the diff)."""
    try:
        g = yaml.safe_load(generated)
        e = yaml.safe_load(expected)
    except yaml.YAMLError:
        return diff_text(generated, expected)
    diffs = diff_structured(g, e)
    if not diffs:
        # Whitespace-only / formatting drift the structural diff misses;
        # show text diff so reviewers can see the actual byte difference.
        return diff_text(generated, expected) if generated != expected else ""
    return "\n".join(diffs)


def diff_json(generated: str, expected: str) -> str:
    """Parse both as JSON and structural-diff. Same fallback semantics as
    diff_yaml."""
    try:
        g = json.loads(generated)
        e = json.loads(expected)
    except json.JSONDecodeError:
        return diff_text(generated, expected)
    diffs = diff_structured(g, e)
    if not diffs:
        return diff_text(generated, expected) if generated != expected else ""
    return "\n".join(diffs)


def diff_file_pair(generated: Path, expected: Path) -> str:
    """Dispatch by extension. Returns "" on equal content."""
    g_text = generated.read_text(encoding="utf-8")
    e_text = expected.read_text(encoding="utf-8")
    suffix = generated.suffix.lower()
    if suffix == ".yaml" or suffix == ".yml":
        return diff_yaml(g_text, e_text)
    if suffix == ".json":
        return diff_json(g_text, e_text)
    if suffix == ".cypher":
        return diff_text(g_text, e_text, context=3)
    return diff_text(g_text, e_text)
