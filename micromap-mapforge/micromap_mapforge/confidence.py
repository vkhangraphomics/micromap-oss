"""Confidence tagging shared across resolve / emit / submit."""

from enum import Enum
from typing import Iterable


class Confidence(str, Enum):
    EXTRACTED = "EXTRACTED"
    INFERRED = "INFERRED"
    AMBIGUOUS = "AMBIGUOUS"


# Strictness order: AMBIGUOUS worst (0), INFERRED (1), EXTRACTED best (2)
_ORDER = {
    Confidence.AMBIGUOUS: 0,
    Confidence.INFERRED: 1,
    Confidence.EXTRACTED: 2,
}


def strictest(values: Iterable[Confidence]) -> Confidence:
    """Return the worst-case confidence across inputs."""
    values = list(values)
    if not values:
        raise ValueError("strictest() requires at least one confidence value")
    return min(values, key=lambda c: _ORDER[c])
