"""Resolver protocol and shared dataclasses."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..confidence import Confidence


@dataclass
class Candidate:
    """One possible match from a resolver."""

    node_id: str
    match_type: Confidence
    score: float                    # 0.0-1.0; higher = better
    reason: str                     # human-readable
    properties: dict[str, Any]      # snapshot of matched node
    merge_field: str = ""           # Cypher key field, e.g. "ncbi_tax_id"
    merge_value: str = ""           # Cypher key value, e.g. "562"


@dataclass
class ResolutionRow:
    """Resolution attempt for a single (label, source_term) pair."""

    entity_label: str               # "Taxon", "Disease", ...
    source_term: str                # raw value from source data
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def is_resolved(self) -> bool:
        """True if there's at least one candidate that isn't AMBIGUOUS."""
        return self.best is not None and self.best.match_type != Confidence.AMBIGUOUS

    @property
    def best(self) -> "Candidate | None":
        """Best candidate for this row, or None if unresolved.

        Ambiguity rule: multiple candidates tied on top score → downgrade to AMBIGUOUS.
        """
        if not self.candidates:
            return None
        ranked = sorted(self.candidates, key=lambda c: c.score, reverse=True)
        top = ranked[0]
        tied = [c for c in ranked if c.score == top.score]
        if len(tied) > 1:
            return Candidate(
                node_id=top.node_id,
                match_type=Confidence.AMBIGUOUS,
                score=top.score,
                reason=f"{len(tied)} candidates tied at score {top.score}",
                properties=top.properties,
                merge_field=top.merge_field,
                merge_value=top.merge_value,
            )
        return top


class EntityResolver(Protocol):
    """Per-node-type resolver. Preloaded once; queried many times."""

    label: str                                      # "Taxon", "Disease", ...

    def resolve(self, term: str, context: dict[str, Any]) -> list[Candidate]:
        """Return ranked candidates for `term`. Empty list = unresolved."""
        ...
