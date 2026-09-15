# micromap-mapforge/micromap_mapforge/resolve/paper.py
"""Paper resolver — PMID/DOI exact, title fuzzy (tight threshold)."""

import re
from typing import Any

from rapidfuzz import fuzz

from ..confidence import Confidence
from .base import Candidate


def _normalize_title(title: str) -> str:
    return re.sub(r"[^\w\s]", " ", title.lower()).strip()


class PaperResolver:
    label = "Paper"

    def __init__(
        self,
        *,
        identifier_fields: list[str],
        primary_id_field: str,
        identifier_index: dict[str, dict[str, dict[str, Any]]],
        title_index: dict[str, list[dict[str, Any]]],
        fuzzy_threshold: float = 0.92,
    ):
        self.identifier_fields = identifier_fields
        self.primary_id_field = primary_id_field
        self.identifier_index = identifier_index
        self.title_index = title_index
        self.fuzzy_threshold = fuzzy_threshold

    def _title_candidate(self, node: dict[str, Any], score: float, reason: str) -> Candidate:
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

        if source_field == "title":
            normalized = _normalize_title(term)
            if not normalized:
                return []
            exact = self.title_index.get(normalized, [])
            if exact:
                return [
                    self._title_candidate(node, 0.97, "normalized-title exact match")
                    for node in exact
                ]

            best_score = 0.0
            best_matches: list[tuple[float, dict[str, Any]]] = []
            for key, nodes in self.title_index.items():
                ratio = fuzz.ratio(normalized, key) / 100.0
                if ratio >= self.fuzzy_threshold and ratio > best_score:
                    best_score = ratio
                    best_matches = [(ratio, node) for node in nodes]
                elif ratio == best_score:
                    best_matches.extend((ratio, node) for node in nodes)

            return [
                self._title_candidate(node, ratio, f"fuzzy-title match (ratio={ratio:.2f})")
                for ratio, node in best_matches
            ]

        return []
