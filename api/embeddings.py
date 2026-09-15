"""Pluggable text embeddings for decision semantic search (#190 pillar 4).

Config-gated by ``EMBEDDINGS_PROVIDER`` (``voyage`` | ``fake`` | unset). When
unset/none the feature is disabled: ingest skips embedding and the semantic
search endpoint reports "not configured", so the existing substring query is
unaffected (mirrors the decision-log gating in pillar 2).

``get_embedder()`` returns the configured backend or ``None``. Every backend
exposes ``dimension`` (the Neo4j vector index is built to match) plus
``embed_documents`` / ``embed_query`` (Voyage distinguishes ``input_type`` for
better retrieval; the fake one ignores it).
"""
from __future__ import annotations

import hashlib
import math
import os
from typing import Optional, Protocol, Sequence, runtime_checkable

# Output dimensions per Voyage model (used to size the Neo4j vector index).
_VOYAGE_DIMS = {
    "voyage-3": 1024,
    "voyage-3-lite": 512,
    "voyage-3.5": 1024,
    "voyage-3.5-lite": 1024,
}
_DEFAULT_VOYAGE_MODEL = "voyage-3-lite"


@runtime_checkable
class Embedder(Protocol):
    dimension: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class VoyageEmbedder:
    """Voyage AI embeddings over HTTP (Anthropic's recommended partner)."""

    def __init__(self, api_key: str, *, model: str = _DEFAULT_VOYAGE_MODEL,
                 dimension: Optional[int] = None, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self.model = model
        self.dimension = dimension or _VOYAGE_DIMS.get(model, 512)
        self._timeout = timeout

    def _embed(self, texts: Sequence[str], input_type: str) -> list[list[float]]:
        import httpx

        resp = httpx.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"input": list(texts), "model": self.model, "input_type": input_type},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        return [d["embedding"] for d in sorted(data, key=lambda x: x["index"])]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "document") if texts else []

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]


class FakeEmbedder:
    """Deterministic, dependency-free embeddings for tests.

    Hashes tokens into a fixed-dim bag-of-words vector (L2-normalized): same text
    → same vector, and texts sharing words are nearer under cosine. NOT
    semantically meaningful — tests only.
    """

    def __init__(self, dimension: int = 64) -> None:
        self.dimension = dimension

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dimension
        for tok in text.lower().split():
            h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
            v[h % self.dimension] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def get_embedder() -> Optional[Embedder]:
    """The configured embedder, or None when semantic search is disabled."""
    provider = (os.environ.get("EMBEDDINGS_PROVIDER") or "").strip().lower()
    if provider == "voyage":
        key = os.environ.get("VOYAGE_API_KEY")
        if not key:
            return None
        dim = os.environ.get("EMBEDDINGS_DIM")
        return VoyageEmbedder(
            key,
            model=os.environ.get("VOYAGE_MODEL", _DEFAULT_VOYAGE_MODEL),
            dimension=int(dim) if dim else None,
        )
    if provider == "fake":
        return FakeEmbedder(dimension=int(os.environ.get("EMBEDDINGS_DIM", "64")))
    return None


def is_enabled() -> bool:
    return get_embedder() is not None
