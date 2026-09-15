"""F4 (#79): ``_parse_endpoint`` must handle nested parens in the endpoint expr.

The original ``_ENDPOINT_RE`` captured ``expr`` as ``[^)]+``, which truncates at
the first inner ``)``. So a wrapped expression like ``Disease(doid=norm(row.x))``
lost its closing paren, and any ``row.column`` positioned *after* an inner ``)``
was dropped entirely (``source_column == ""``).
"""
from micromap_mapforge.integration.tabular.to_ir import _ENDPOINT_RE, _parse_endpoint


def test_simple_endpoint_unchanged():
    # Regression guard: the common single-paren form is unaffected by the fix.
    assert _parse_endpoint("OrganismTaxon(id=row.from)") == {
        "label": "OrganismTaxon",
        "field": "id",
        "source_column": "from",
    }


def test_nested_parens_full_expr_captured():
    # expr must include the inner balanced parens, not truncate at the first ')'.
    m = _ENDPOINT_RE.match("Disease(doid=norm(row.disease))")
    assert m is not None
    assert m.group("expr") == "norm(row.disease)"


def test_nested_parens_source_column():
    assert _parse_endpoint("Disease(doid=norm(row.disease))") == {
        "label": "Disease",
        "field": "doid",
        "source_column": "disease",
    }


def test_column_after_inner_paren_not_dropped():
    # Before the fix, expr truncated to 'fn(a' and the column after the inner
    # ')' was lost (source_column == ''). Full-expr capture recovers it.
    assert _parse_endpoint("Disease(doid=fn(a) row.disease)")["source_column"] == "disease"
