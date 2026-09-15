"""Cross-source entity unification hints — D4 (#77).

Bioregistry-canonical IDs already dedup nodes *within* a BioCypher run (same
``label`` + ``id``). D4 is the *cross-run* case: source A's ``Compound`` keyed on
a DrugBank ID and source B's keyed on a ChEMBL ID may be the same molecule.

``propose_same_as`` compares two bundle IR snapshots (the ``bundle.ir.json`` that
``serialize_bundle`` writes, #76 C8) and surfaces SAME_AS candidates: two nodes
of the same Biolink category, with *different* primary ids, that share an
identifier value. CURIE prefixes are normalized via Bioregistry, so ``chembl:X``
and ``CHEMBL:X`` match. Candidates are hints for review — the host owns the link.

(Pure cross-prefix resolution with no shared id — DrugBank↔ChEMBL via UniChem —
is a later increment; this surfaces the cases where a shared xref already exists.)
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .versioning import load_ir_snapshot

# Property keys that denote an identifier (besides CURIE-shaped values).
_ID_KEY_RE = re.compile(r"(^id$|_id$|_curie$|_accession$|^accession$|inchikey|^unii$|^cas$)", re.I)
_CURIE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.]*:[^:\s]+$")


def _normalize_prefix(prefix: str) -> str:
    try:
        import bioregistry
        return (bioregistry.normalize_prefix(prefix) or prefix).lower()
    except Exception:
        return prefix.lower()


def _normalize_token(value: str) -> str:
    if _CURIE_RE.match(value):
        prefix, _, local = value.partition(":")
        return f"{_normalize_prefix(prefix)}:{local}"
    return value


_KEY_PREFIX_RE = re.compile(r"^(.*?)(?:_id|_curie|_accession)$", re.I)


def _identity_tokens(node: dict) -> set[str]:
    """Identifier-ish values for a node: its id + CURIE-shaped / id-keyed props.

    For a bare id-keyed value (``chembl_id = CHEMBL25``) we also synthesize the
    CURIE ``chembl:CHEMBL25`` from the key prefix, so it unifies with another
    source keyed on the CURIE form ``CHEMBL:CHEMBL25``.
    """
    tokens = {_normalize_token(str(node.get("id", "")))}
    for k, v in (node.get("properties") or {}).items():
        if v is None or v == "":
            continue
        sv = str(v)
        is_curie = bool(_CURIE_RE.match(sv))
        if not (_ID_KEY_RE.search(k) or is_curie):
            continue
        tokens.add(_normalize_token(sv))
        if not is_curie:
            m = _KEY_PREFIX_RE.match(k)
            if m and m.group(1):
                tokens.add(f"{_normalize_prefix(m.group(1))}:{sv}")
    tokens.discard("")
    return tokens


def propose_same_as(snap_a: dict, snap_b: dict) -> list[dict[str, Any]]:
    """SAME_AS candidates between two IR snapshots, sorted deterministically."""
    b_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for n in snap_b.get("nodes", []):
        for tok in _identity_tokens(n):
            b_index[(n.get("label"), tok)].append(n)

    candidates: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for a in snap_a.get("nodes", []):
        shared_by_b: dict[str, set[str]] = defaultdict(set)
        for tok in _identity_tokens(a):
            for b in b_index.get((a.get("label"), tok), []):
                if b.get("id") == a.get("id"):
                    continue  # identical primary id == within-run dedup, not cross-source
                shared_by_b[b["id"]].add(tok)
        for b_id, shared in shared_by_b.items():
            key = (a.get("label"), a.get("id"), b_id)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "label": a.get("label"),
                "a_id": a.get("id"),
                "b_id": b_id,
                "shared": sorted(shared),
            })
    return sorted(candidates, key=lambda c: (c["label"] or "", c["a_id"] or "", c["b_id"] or ""))


def propose_same_as_dirs(dir_a: str | Path, dir_b: str | Path) -> list[dict[str, Any]]:
    return propose_same_as(load_ir_snapshot(dir_a), load_ir_snapshot(dir_b))
