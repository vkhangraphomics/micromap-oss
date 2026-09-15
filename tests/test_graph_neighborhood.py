"""Regression tests for the /graph/neighborhood traversal query.

The endpoint returned 500 on every input because its variable-length pattern
used a negated relationship-type expression (`-[r:!HAS_PARENT|EFFECTIVE_AGAINST *1..n]-`),
which is invalid Cypher — var-length patterns only accept a positive union of
types. These tests pin the corrected query construction so the bug can't recur.

Note: these assert on the generated Cypher (no live Neo4j here). End-to-end
verification requires hitting the deployed endpoint.
"""

from api.routes.graph import _NEIGHBORHOOD_REL_TYPES, _neighborhood_query

EXCLUDED = ("HAS_PARENT", "EFFECTIVE_AGAINST")


def test_query_has_no_negated_relationship_expression():
    """The `!` negation in a variable-length pattern is what caused the 500s."""
    q = _neighborhood_query(2)
    assert ":!" not in q, f"negated var-length rel expression reintroduced:\n{q}"


def test_query_uses_positive_union_of_allowed_types():
    q = _neighborhood_query(2)
    for rel in _NEIGHBORHOOD_REL_TYPES:
        assert rel in q, f"allowed rel type {rel} missing from query"
    # Union is wired into the variable-length pattern.
    assert "*1..2]-(neighbor)" in q


def test_query_excludes_hierarchy_and_amr_edges():
    """HAS_PARENT and EFFECTIVE_AGAINST must not be traversed (they explode)."""
    q = _neighborhood_query(3)
    for rel in EXCLUDED:
        assert rel not in q, f"{rel} must be excluded from neighborhood traversal"


def test_max_depth_is_interpolated():
    assert "*1..1]-(neighbor)" in _neighborhood_query(1)
    assert "*1..3]-(neighbor)" in _neighborhood_query(3)


def test_no_distinct_aggregate_mix():
    """The old query mixed `WITH DISTINCT` with aggregates; the fix groups cleanly."""
    assert "WITH DISTINCT" not in _neighborhood_query(2)
