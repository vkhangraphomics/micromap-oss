"""#266: mapping-quality scorer.

Compare a produced ``mapping.yaml`` dict (from ``draft_heuristic_mapping`` or
``propose_mapping``) against a hand-curated gold mapping, and report quality on
the three axes a contributor actually cares about:

- **entities** — did we recover the right node labels? (precision/recall/F1)
- **columns** — did we map the right source column to the right ontology field?
  scored over ``(label, field, source_col)`` triples, plus a separate
  ``match_on`` accuracy (did we pick the right key column per label).
- **relationships** — did we recover the graph edges? scored by
  ``(type, from-label, to-label)`` signature, ignoring the property payload
  (a separate concern). The heuristic never invents relationships, so its
  relationship recall is 0 by construction — which is exactly the number the
  eval exists to make visible.

Pure and deterministic: no I/O, no LLM. This is the shared measuring stick for
both mapping arms.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PRF:
    """Precision / recall / F1 for one axis."""
    precision: float
    recall: float
    f1: float


@dataclass(frozen=True)
class MappingScore:
    entities: PRF          # entity-label recovery
    match_on_accuracy: float   # of gold labels also produced, fraction with correct match_on
    columns: PRF           # (label, field, source_col) triple recovery
    relationships: PRF     # (type, from-label, to-label) signature recovery


def _prf(pred: set, gold: set) -> PRF:
    """P/R/F1 over two sets. Empty-set conventions: precision is 1.0 when nothing
    was predicted (nothing wrong was said); recall is 1.0 when there is nothing to
    find. F1 is the harmonic mean, 0 when either P or R is 0."""
    tp = len(pred & gold)
    precision = tp / len(pred) if pred else 1.0
    recall = tp / len(gold) if gold else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return PRF(precision, recall, f1)


def _entities(mapping: dict) -> list[dict]:
    return mapping.get("entities") or []


def _labels(mapping: dict) -> set[str]:
    return {e["label"] for e in _entities(mapping) if "label" in e}


def _column_triples(mapping: dict) -> set[tuple[str, str, str]]:
    """(label, ontology_field, source_column) over every entity column mapping."""
    triples: set[tuple[str, str, str]] = set()
    for e in _entities(mapping):
        label = e.get("label")
        for field, col in (e.get("columns") or {}).items():
            triples.add((label, field, col))
    return triples


def _endpoint_label(endpoint: str) -> str:
    """'Taxon(ncbi_tax_id=row.x)' -> 'Taxon'. A bare 'Taxon' returns itself."""
    return endpoint.split("(", 1)[0].strip()


def _rel_signatures(mapping: dict) -> set[tuple[str, str, str]]:
    """(type, from-label, to-label) per relationship — the graph edge shape,
    independent of the property payload."""
    sigs: set[tuple[str, str, str]] = set()
    for r in (mapping.get("relationships") or []):
        sigs.add((
            r.get("type", ""),
            _endpoint_label(str(r.get("from", ""))),
            _endpoint_label(str(r.get("to", ""))),
        ))
    return sigs


def _match_on_accuracy(produced: dict, gold: dict) -> float:
    """Of the gold labels the producer also emitted, the fraction whose match_on
    field equals gold's. Labels the producer missed are counted by entity recall,
    not here — this isolates 'given it found the entity, did it pick the key'."""
    gold_match_on = {e["label"]: e.get("match_on") for e in _entities(gold)}
    prod_match_on = {e["label"]: e.get("match_on") for e in _entities(produced)}
    shared = [lbl for lbl in gold_match_on if lbl in prod_match_on]
    if not shared:
        return 0.0 if gold_match_on else 1.0
    correct = sum(1 for lbl in shared if prod_match_on[lbl] == gold_match_on[lbl])
    return correct / len(shared)


def score_mapping(produced: dict, gold: dict) -> MappingScore:
    """Score a produced mapping against a gold mapping. See module docstring."""
    return MappingScore(
        entities=_prf(_labels(produced), _labels(gold)),
        match_on_accuracy=_match_on_accuracy(produced, gold),
        columns=_prf(_column_triples(produced), _column_triples(gold)),
        relationships=_prf(_rel_signatures(produced), _rel_signatures(gold)),
    )
