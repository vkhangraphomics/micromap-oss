"""#314: the resolver preload is scoped to the caller's org plus canonical orgs.

The fake session here APPLIES the org predicate from the bound parameters, so
these tests prove filtering semantics — a caller cannot resolve onto another
org's node — not just the shape of the emitted Cypher.
"""
from unittest.mock import MagicMock

from micromap_mapforge.resolve.registry import build_resolvers

_TAXA = [
    {"node_id": "NCBI:100", "ncbi_tax_id": "100",
     "scientific_name": "Defaultia communis", "organization_id": "default"},
    {"node_id": "NCBI:200", "ncbi_tax_id": "200",
     "scientific_name": "Ownia propria", "organization_id": "org-a"},
    {"node_id": "NCBI:300", "ncbi_tax_id": "300",
     "scientific_name": "Aliena secreta", "organization_id": "org-b"},
]


def _org_aware_driver(records_by_label: dict, captured: list) -> MagicMock:
    driver = MagicMock()
    session_ctx = MagicMock()
    session = MagicMock()
    driver.session.return_value = session_ctx
    session_ctx.__enter__ = MagicMock(return_value=session)
    session_ctx.__exit__ = MagicMock(return_value=False)

    def run(cypher, parameters=None, **kwargs):
        params = dict(parameters or {})
        params.update(kwargs)
        captured.append((cypher, params))
        rows: list[dict] = []
        for label, label_rows in records_by_label.items():
            if f"(n:{label})" in cypher:
                rows = list(label_rows)
                break
        if "organization_id" in cypher:
            org = params.get("org")
            canonical = params.get("canonical_orgs") or []
            rows = [
                r for r in rows
                if r.get("organization_id") == org
                or r.get("organization_id") in canonical
            ]
        result = MagicMock()
        result.__iter__ = lambda self, _r=rows: iter(_r)
        result.data.return_value = rows
        return result

    session.run = run
    return driver


def test_scoped_resolver_cannot_resolve_another_orgs_node(monkeypatch):
    monkeypatch.delenv("CANONICAL_ORGS", raising=False)
    monkeypatch.delenv("PUBLIC_ORGS", raising=False)
    captured: list = []
    driver = _org_aware_driver({"Taxon": _TAXA}, captured)
    resolvers = build_resolvers(
        driver, database="neo4j", labels_of_interest={"Taxon"},
        organization_id="org-a",
    )
    taxon = resolvers["Taxon"]
    assert len(taxon.resolve("200", {"source_field": "ncbi_tax_id"})) == 1
    assert len(taxon.resolve("100", {"source_field": "ncbi_tax_id"})) == 1
    assert taxon.resolve("300", {"source_field": "ncbi_tax_id"}) == []


def test_unscoped_preload_when_org_none():
    """organization_id=None preserves the standalone-CLI behavior: no
    predicate at all, every node preloads."""
    captured: list = []
    driver = _org_aware_driver({"Taxon": _TAXA}, captured)
    resolvers = build_resolvers(
        driver, database="neo4j", labels_of_interest={"Taxon"},
    )
    taxon = resolvers["Taxon"]
    assert len(taxon.resolve("300", {"source_field": "ncbi_tax_id"})) == 1
    assert captured, "no preload query was issued"
    assert all("organization_id" not in cypher for cypher, _ in captured)


def test_org_is_bound_as_parameter_never_interpolated(monkeypatch):
    monkeypatch.setenv("CANONICAL_ORGS", "default,public-ref")
    hostile_org = "org-a'}) MATCH (m) DETACH DELETE m //"
    captured: list = []
    driver = _org_aware_driver({"Taxon": _TAXA}, captured)
    build_resolvers(
        driver, database="neo4j", labels_of_interest={"Taxon"},
        organization_id=hostile_org,
    )
    assert captured
    for cypher, params in captured:
        assert hostile_org not in cypher
        assert params["org"] == hostile_org
        assert set(params["canonical_orgs"]) == {"default", "public-ref"}
