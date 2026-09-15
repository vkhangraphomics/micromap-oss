"""Pluggable embedder for decision semantic search (#190 pillar 4)."""
import httpx
import respx

from api import embeddings


def _cos(u, v):
    # both vectors are L2-normalized, so cosine == dot product
    return sum(a * b for a, b in zip(u, v, strict=True))


def test_fake_embedder_deterministic_and_dim():
    e = embeddings.FakeEmbedder(dimension=32)
    a = e.embed_query("parkinson butyrate pathway")
    assert a == e.embed_query("parkinson butyrate pathway")
    assert len(a) == 32
    docs = e.embed_documents(["x y", "z"])
    assert len(docs) == 2 and len(docs[0]) == 32


def test_fake_embedder_shared_words_are_nearer():
    e = embeddings.FakeEmbedder(dimension=256)
    q = e.embed_query("parkinson butyrate guild")
    near = e.embed_documents(["parkinson butyrate producing guild depleted"])[0]
    far = e.embed_documents(["quarterly revenue headcount planning"])[0]
    assert _cos(q, near) > _cos(q, far)


def test_get_embedder_honors_env(monkeypatch):
    monkeypatch.delenv("EMBEDDINGS_PROVIDER", raising=False)
    assert embeddings.get_embedder() is None
    assert embeddings.is_enabled() is False

    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDINGS_DIM", "48")
    emb = embeddings.get_embedder()
    assert isinstance(emb, embeddings.FakeEmbedder) and emb.dimension == 48

    # voyage selected but no key -> disabled (None), not a crash
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "voyage")
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDINGS_DIM", raising=False)
    assert embeddings.get_embedder() is None

    monkeypatch.setenv("VOYAGE_API_KEY", "vk-test")
    monkeypatch.setenv("VOYAGE_MODEL", "voyage-3")
    emb = embeddings.get_embedder()
    assert isinstance(emb, embeddings.VoyageEmbedder)
    assert emb.dimension == 1024  # voyage-3 default dim


def test_voyage_embedder_request_shape_and_order():
    with respx.mock(base_url="https://api.voyageai.com") as mock:
        route = mock.post("/v1/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [
                # deliberately out of order; the embedder must sort by index
                {"embedding": [0.3, 0.4], "index": 1},
                {"embedding": [0.1, 0.2], "index": 0},
            ]})
        )
        e = embeddings.VoyageEmbedder("vk-test", model="voyage-3-lite", dimension=2)
        out = e.embed_documents(["alpha", "beta"])

    assert out == [[0.1, 0.2], [0.3, 0.4]]
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer vk-test"
    import json
    body = json.loads(req.content)
    assert body["model"] == "voyage-3-lite"
    assert body["input_type"] == "document"
    assert body["input"] == ["alpha", "beta"]


def test_voyage_embed_query_uses_query_input_type():
    with respx.mock(base_url="https://api.voyageai.com") as mock:
        route = mock.post("/v1/embeddings").mock(
            return_value=httpx.Response(200, json={"data": [{"embedding": [1.0], "index": 0}]})
        )
        embeddings.VoyageEmbedder("vk-test", dimension=1).embed_query("why target X")
    import json
    assert json.loads(route.calls.last.request.content)["input_type"] == "query"
