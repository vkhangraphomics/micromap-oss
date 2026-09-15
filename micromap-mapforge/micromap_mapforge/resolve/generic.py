# micromap-mapforge/micromap_mapforge/resolve/generic.py
"""Generic canonical-ID + exact-name resolver.

Covers node types where resolution is straightforward: match on a canonical
identifier field, or fall back to a name field via case-folded exact lookup.
"""

from typing import Any

from ..confidence import Confidence
from .base import Candidate


class GenericResolver:
    label: str

    def __init__(
        self,
        *,
        label: str,
        identifier_fields: list[str],
        name_field: str | None,
        primary_id_field: str,
        identifier_index: dict[str, dict[str, dict[str, Any]]],
        name_index: dict[str, list[dict[str, Any]]],
    ):
        """
        identifier_index: {identifier_name: {id_value: node_props}}
        name_index: {lowercased_name: [node_props, ...]}
        primary_id_field: the canonical identifier field, used as MERGE key
        """
        self.label = label
        self.identifier_fields = identifier_fields
        self.name_field = name_field
        self.primary_id_field = primary_id_field
        self.identifier_index = identifier_index
        self.name_index = name_index

    def _candidate_from_node(
        self,
        node: dict[str, Any],
        *,
        match_type: Confidence,
        score: float,
        reason: str,
        source_field: str,
    ) -> Candidate:
        # Merge key: prefer source_field if it's a valid identifier on the node,
        # otherwise fall back to the primary_id_field.
        if source_field in self.identifier_fields and node.get(source_field) is not None:
            merge_field = source_field
        else:
            merge_field = self.primary_id_field
        merge_value = str(node.get(merge_field, ""))
        node_id = f"{merge_field}:{merge_value}"
        return Candidate(
            node_id=node_id,
            match_type=match_type,
            score=score,
            reason=reason,
            properties=node,
            merge_field=merge_field,
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
                return [self._candidate_from_node(
                    node,
                    match_type=Confidence.EXTRACTED,
                    score=1.0,
                    reason=f"exact {source_field} match",
                    source_field=source_field,
                )]
            return []

        if source_field == self.name_field:
            key = term.strip().lower()
            nodes = self.name_index.get(key, [])
            return [
                self._candidate_from_node(
                    node,
                    match_type=Confidence.INFERRED,
                    score=0.9,
                    reason=f"exact {self.name_field} match (case-folded)",
                    source_field=source_field,
                )
                for node in nodes
            ]

        return []
