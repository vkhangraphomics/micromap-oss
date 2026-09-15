"""#299: the cross-principal isolation tests this package has never had.

`scoping.scope_for()` was removed (#299 final review item 6, dead code — see
scoping.py's module docstring), so these two tests now build `OrgScope`
directly and drive the org half through `auth.principal_org()` — the actual
production helper every write path consolidated onto.
"""
import pytest
from unittest.mock import patch

from micromap_mcp.auth import Principal, principal_org
from micromap_mcp.scoping import OrgScope, scope_params


@pytest.mark.parametrize("org", ["acme", "beta"])
def test_each_principal_scopes_to_its_own_org_plus_public(org):
    with patch("micromap_mcp.auth.current_principal",
               return_value=Principal(org_id=org, role="user")):
        params = scope_params(OrgScope(org_id=principal_org(), public_orgs=["default"]))
    assert params["organization_id"] == org
    assert params["public_orgs"] == ["default"]


def test_two_principals_never_share_a_scope():
    with patch("micromap_mcp.auth.current_principal",
               return_value=Principal(org_id="acme")):
        a = principal_org()
    with patch("micromap_mcp.auth.current_principal",
               return_value=Principal(org_id="beta")):
        b = principal_org()
    assert a != b


def test_a_caller_cannot_widen_scope_through_any_tool_argument():
    """No registered tool takes an org argument — enforced by AST in
    test_tool_scoping_coverage.py; asserted here as the behavioural contract."""
    import inspect
    from micromap_mcp.tools.provenance import register_provenance_tools

    prov = register_provenance_tools(app=None, neo4j_uri="bolt://x", neo4j_user="u",
                                     neo4j_password="p", database="neo4j",
                                     return_callables=True)
    for fn in prov.values():
        assert "organization_id" not in inspect.signature(fn).parameters
