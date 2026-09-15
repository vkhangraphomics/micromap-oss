"""Tests for bounded resolver registry preload (GH#70).

`MATCH (n:Label) RETURN properties(n)` returns the full property dict for
every node — at MicroMap scale (millions of taxa, papers) this OOMs the
resolver. The registry must instead project only the identifier fields
and the name field declared in ontology.yaml, and stream results so peak
memory stays bounded.
"""

from unittest.mock import MagicMock

from micromap_mapforge.resolve.registry import _fetch_nodes, build_resolvers


def _capture_session(records_by_label: dict[str, list[dict]]):
    """Build a fake Neo4j driver that records the queries it was asked to run."""
    queries: list[str] = []
    driver = MagicMock()
    session_ctx = MagicMock()
    session = MagicMock()
    driver.session.return_value = session_ctx
    session_ctx.__enter__ = MagicMock(return_value=session)
    session_ctx.__exit__ = MagicMock(return_value=False)

    def run(cypher, **kwargs):
        queries.append(cypher)
        for label in records_by_label:
            if f"(n:{label})" in cypher:
                # New projected-field shape: each record IS the flat dict.
                rows = list(records_by_label[label])
                result = MagicMock()
                result.data.return_value = rows
                result.__iter__ = lambda self, _r=rows: iter(_r)
                return result
        result = MagicMock()
        result.data.return_value = []
        result.__iter__ = lambda self: iter([])
        return result

    session.run = run
    return driver, queries


def test_query_projects_named_fields_not_full_properties():
    """`properties(n)` is unbounded; the query must enumerate only the
    identifier fields and name_field declared in ontology.yaml."""
    driver, queries = _capture_session({"Taxon": []})
    _fetch_nodes(driver, "Taxon", "neo4j")
    assert len(queries) == 1
    q = queries[0]
    # Forbidden: full-property dump.
    assert "properties(n)" not in q
    # Required: explicit projection of Taxon's ontology-declared fields.
    # ontology.yaml: identifiers=[taxon_id, ncbi_tax_id, gtdb_id], name_field=name
    for field in ("taxon_id", "ncbi_tax_id", "gtdb_id", "name"):
        assert f"n.{field}" in q, f"projection missing n.{field}; query was: {q!r}"


def test_query_projects_disease_fields():
    """Disease has many alt identifiers + name; all must be projected."""
    driver, queries = _capture_session({"Disease": []})
    _fetch_nodes(driver, "Disease", "neo4j")
    q = queries[0]
    assert "properties(n)" not in q
    for field in ("name_normalized", "disease_id", "doid", "mesh_id", "name"):
        assert f"n.{field}" in q, f"projection missing n.{field}"


def test_returned_props_contain_only_projected_fields():
    """Even if a node has 50 properties on the server side, the in-process
    props dict must contain only the projected identifier+name fields, so
    memory grows with field-count not property-count."""
    # Simulate Neo4j returning only the projected slice (the real driver
    # would already have applied the projection on the server side).
    driver, _ = _capture_session({
        "Taxon": [
            {"taxon_id": "NCBI:562", "ncbi_tax_id": "562", "gtdb_id": None,
             "name": "Escherichia coli"},
        ],
    })
    nodes = _fetch_nodes(driver, "Taxon", "neo4j")
    assert len(nodes) == 1
    keys = set(nodes[0].keys())
    # Only ontology-declared fields, nothing else.
    assert keys.issubset({"taxon_id", "ncbi_tax_id", "gtdb_id", "name"}), (
        f"unexpected keys leaked: {keys}"
    )


def test_build_resolvers_still_yields_working_resolvers():
    """Backwards-compat: existing resolver behavior must keep working under
    the projected-fields shape."""
    driver, _ = _capture_session({
        "Taxon": [{"taxon_id": "NCBI:562", "ncbi_tax_id": "562",
                   "gtdb_id": None, "name": "Escherichia coli"}],
        "Disease": [{"name_normalized": "crohns disease", "disease_id": None,
                     "doid": "8778", "mesh_id": None, "omim_id": None,
                     "umls_cui": None, "icd10": None, "icd10_code": None,
                     "mondo_id": None, "name": "Crohn's Disease"}],
    })
    resolvers = build_resolvers(driver, database="neo4j")
    cands = resolvers["Taxon"].resolve("562", {"source_field": "ncbi_tax_id"})
    assert len(cands) == 1
    assert cands[0].merge_value == "562"


# --- #117: scope the preload to mapping-referenced labels ---


def _queried_labels(queries: list[str]) -> set[str]:
    """Extract the set of labels queried from a list of `MATCH (n:Label) ...` strings."""
    import re

    labels: set[str] = set()
    for q in queries:
        m = re.search(r"MATCH \(n:(\w+)\)", q)
        if m:
            labels.add(m.group(1))
    return labels


def test_build_resolvers_default_preloads_all_ontology_labels():
    """Backwards-compat: when labels_of_interest is None (default), every label
    in ontology.yaml is preloaded — the pre-#117 behavior. Existing callers
    that pass no labels see no behavior change.
    """
    driver, queries = _capture_session({})
    build_resolvers(driver, database="neo4j")
    queried = _queried_labels(queries)
    # ontology.yaml declares these — assert the preload covered all of them.
    expected = {"Taxon", "Disease", "Compound", "Drug", "Gene",
                "Protein", "Pathway", "Paper", "BodySite"}
    missing = expected - queried
    assert not missing, f"default preload should cover all ontology labels; missing: {missing}"


def test_build_resolvers_scopes_preload_to_labels_of_interest():
    """#117: when the caller declares the set of labels actually used by the
    bundle's mapping, the preload skips every other ontology label.

    Disbiome's mapping uses Taxon, Disease, Paper. Without this scoping, the
    resolver also queried Gene, Protein, Pathway, BodySite, etc. and Neo4j
    flooded stderr with `01N42` notifications for missing properties — log
    noise that buried real diagnostics.
    """
    driver, queries = _capture_session({})
    build_resolvers(driver, database="neo4j", labels_of_interest={"Taxon", "Disease", "Paper"})

    queried = _queried_labels(queries)
    assert queried == {"Taxon", "Disease", "Paper"}, (
        f"expected preload exactly {{Taxon, Disease, Paper}}; got {queried}"
    )

    # Spot-check: a label that's in the ontology but NOT in the scope was NOT queried.
    assert "Gene" not in queried
    assert "Protein" not in queried
    assert "Pathway" not in queried


def test_build_resolvers_silently_skips_labels_outside_ontology():
    """A label that isn't in ontology.yaml (e.g., a typo or a custom label) is
    silently dropped from the scope. The set intersection with the ontology
    decides what actually gets preloaded — the registry never queries Neo4j
    for a label it has no resolver for.
    """
    driver, queries = _capture_session({})
    build_resolvers(
        driver,
        database="neo4j",
        labels_of_interest={"Taxon", "Movie", "NotInOntology"},
    )
    queried = _queried_labels(queries)
    assert queried == {"Taxon"}, (
        f"expected only Taxon (the only intersection with ontology); got {queried}"
    )


def test_build_resolvers_empty_labels_of_interest_preloads_nothing():
    """Explicit empty set = preload no labels. Edge case but well-defined."""
    driver, queries = _capture_session({})
    resolvers = build_resolvers(driver, database="neo4j", labels_of_interest=set())
    assert _queried_labels(queries) == set()
    assert resolvers == {}
