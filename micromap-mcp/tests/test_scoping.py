"""#299: the MCP mirror of api/scoping.py.

Only `OrgScope`, `ORG_FILTER`, and `scope_params` are covered here —
`public_orgs()` / `scope_for()` were removed (#299 final review item 6) as
dead code with no production caller; see scoping.py's module docstring.
"""
from micromap_mcp.scoping import ORG_FILTER, OrgScope, scope_params


def test_org_filter_binds_values_as_parameters_not_text():
    """The un-widenability property: only the VARIABLE name is interpolated."""
    f = ORG_FILTER("n")
    assert f == ("(n.organization_id = $organization_id "
                 "OR n.organization_id IN $public_orgs)")
    assert "acme" not in f


def test_scope_params_shape():
    s = OrgScope(org_id="acme", public_orgs=["default"])
    assert scope_params(s) == {"organization_id": "acme", "public_orgs": ["default"]}
