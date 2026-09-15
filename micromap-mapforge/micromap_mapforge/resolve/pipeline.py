"""Resolve a validated mapping over source rows into a ResolutionReport."""

from dataclasses import dataclass, field
from typing import Any, Iterable

from .base import EntityResolver, ResolutionRow


@dataclass
class ResolutionReport:
    resolved: list[ResolutionRow] = field(default_factory=list)
    unresolved: list[ResolutionRow] = field(default_factory=list)
    ambiguous: list[ResolutionRow] = field(default_factory=list)

    @property
    def resolved_count(self) -> int:
        return len(self.resolved)

    @property
    def unresolved_count(self) -> int:
        return len(self.unresolved)

    @property
    def ambiguous_count(self) -> int:
        return len(self.ambiguous)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": [_row_to_dict(r) for r in self.resolved],
            "unresolved": [_row_to_dict(r) for r in self.unresolved],
            "ambiguous": [_row_to_dict(r) for r in self.ambiguous],
            "resolved_count": self.resolved_count,
            "unresolved_count": self.unresolved_count,
            "ambiguous_count": self.ambiguous_count,
        }


def _row_to_dict(row: ResolutionRow) -> dict[str, Any]:
    return {
        "entity_label": row.entity_label,
        "source_term": row.source_term,
        "candidates": [
            {
                "node_id": c.node_id,
                "match_type": c.match_type.value,
                "score": c.score,
                "reason": c.reason,
                "merge_field": c.merge_field,
                "merge_value": c.merge_value,
            }
            for c in row.candidates
        ],
    }


def resolve_mapping(
    mapping: dict[str, Any],
    rows: Iterable[dict[str, Any]],
    resolvers: dict[str, EntityResolver],
) -> ResolutionReport:
    """Apply each entity mapping to every source row; dedupe by (label, source_term)."""
    from ..confidence import Confidence

    report = ResolutionReport()
    seen: set[tuple[str, str]] = set()
    all_rows = list(rows)

    for entity_mapping in mapping.get("entities", []):
        label = entity_mapping["label"]
        match_on = entity_mapping["match_on"]
        columns = entity_mapping["columns"]
        source_column = columns.get(match_on)
        if source_column is None:
            # match_on isn't in the column map — pick first column as fallback
            source_column = next(iter(columns.values()))
        resolver = resolvers.get(label)
        if resolver is None:
            continue

        for row in all_rows:
            raw = row.get(source_column)
            if raw is None or raw == "":
                continue
            term = str(raw)
            key = (label, term)
            if key in seen:
                continue
            seen.add(key)

            candidates = resolver.resolve(term, {"source_field": match_on})
            res_row = ResolutionRow(
                entity_label=label, source_term=term, candidates=candidates
            )
            best = res_row.best
            if best is None:
                report.unresolved.append(res_row)
            elif best.match_type == Confidence.AMBIGUOUS:
                report.ambiguous.append(res_row)
            else:
                report.resolved.append(res_row)

    return report
