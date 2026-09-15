"""Generalized exact-id + normalized-name + bounded-fuzzy resolver.

The strategy a new domain selects via `x_mapforge.resolver.strategy: fuzzy`.
Generalizes DiseaseResolver/PaperResolver: the normalizer is injected (a
Callable or None) instead of hardcoded. Does NOT replace those two classes.
"""

from typing import Any, Callable

from rapidfuzz import fuzz

from ..confidence import Confidence
from .base import Candidate


class FuzzyResolver:
    label: str

    def __init__(
        self,
        *,
        label: str,
        name_field: str | None,
        identifier_fields: list[str],
        primary_id_field: str,
        identifier_index: dict[str, dict[str, dict[str, Any]]],
        name_index_normalized: dict[str, list[dict[str, Any]]],
        normalizer: Callable[[str], str] | None = None,
        fuzzy_threshold: float = 0.85,
    ):
        self.label = label
        self.name_field = name_field
        self.identifier_fields = identifier_fields
        self.primary_id_field = primary_id_field
        self.identifier_index = identifier_index
        self.name_index_normalized = name_index_normalized
        self._normalize = normalizer or (lambda s: s.strip().lower())
        self.fuzzy_threshold = fuzzy_threshold

    def _name_candidate(self, node: dict[str, Any], score: float, reason: str) -> Candidate:
        merge_value = str(node.get(self.primary_id_field, ""))
        return Candidate(
            node_id=f"{self.primary_id_field}:{merge_value}",
            match_type=Confidence.INFERRED,
            score=score,
            reason=reason,
            properties=node,
            merge_field=self.primary_id_field,
            merge_value=merge_value,
        )

    def resolve(self, term: str, context: dict[str, Any]) -> list[Candidate]:
        if not term:
            return []
        source_field = context.get("source_field", "")

        if source_field in self.identifier_fields:
            bucket = self.identifier_index.get(source_field, {})
            node = bucket.get(str(term))
            if node:
                return [Candidate(
                    node_id=f"{source_field}:{term}",
                    match_type=Confidence.EXTRACTED,
                    score=1.0,
                    reason=f"exact {source_field} match",
                    properties=node,
                    merge_field=source_field,
                    merge_value=str(term),
                )]
            return []

        if self.name_field and source_field == self.name_field:
            normalized = self._normalize(str(term))
            if not normalized:
                return []

            exact = self.name_index_normalized.get(normalized, [])
            if exact:
                return [
                    self._name_candidate(node, 0.95, f"normalized-name match ({normalized!r})")
                    for node in exact
                ]

            best_score = 0.0
            best_matches: list[tuple[float, dict[str, Any]]] = []
            for key, nodes in self.name_index_normalized.items():
                ratio = fuzz.ratio(normalized, key) / 100.0
                if ratio >= self.fuzzy_threshold and ratio > best_score:
                    best_score = ratio
                    best_matches = [(ratio, node) for node in nodes]
                elif ratio == best_score and best_score > 0.0:
                    best_matches.extend((ratio, node) for node in nodes)

            return [
                self._name_candidate(node, ratio, f"fuzzy-normalized match (ratio={ratio:.2f})")
                for ratio, node in best_matches
            ]

        return []
