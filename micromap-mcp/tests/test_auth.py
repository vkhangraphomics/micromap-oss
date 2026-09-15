import time
import jwt
from micromap_mcp import auth


def _tok(claims, secret="s3cret", **over):
    payload = {"exp": int(time.time()) + 300, **claims}
    payload.update(over)
    return jwt.encode(payload, secret, algorithm="HS256")


def test_jwt_disabled_without_material(monkeypatch):
    for k in ("JWT_SECRET", "JWT_PUBLIC_KEY", "JWKS_URL"):
        monkeypatch.delenv(k, raising=False)
    assert auth.jwt_enabled() is False
    assert auth.verify_workbench_jwt(_tok({"orgId": "acme"})) is None


def test_hs256_valid_claims(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    monkeypatch.delenv("JWT_AUDIENCE", raising=False)
    monkeypatch.delenv("JWT_ISSUER", raising=False)
    claims = auth.verify_workbench_jwt(_tok({"orgId": "acme", "sub": "u1", "roles": ["admin", "member"]}))
    assert claims is not None
    p = auth.principal_from_claims(claims)
    assert (p.org_id, p.user_id, p.role, p.auth) == ("acme", "u1", "admin", "jwt")
    assert auth.roles_from_claims(claims) == ["admin", "member"]


def test_roles_as_string(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    claims = auth.verify_workbench_jwt(_tok({"orgId": "acme", "roles": "service"}))
    assert auth.roles_from_claims(claims) == ["service"]
    assert auth.principal_from_claims(claims).is_service is True


def test_expired_returns_none(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    assert auth.verify_workbench_jwt(_tok({"orgId": "acme"}, exp=int(time.time()) - 10)) is None


def test_bad_signature_returns_none(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    assert auth.verify_workbench_jwt(_tok({"orgId": "acme"}, secret="wrong")) is None


def test_audience_enforced_when_configured(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    monkeypatch.setenv("JWT_AUDIENCE", "micromap")
    assert auth.verify_workbench_jwt(_tok({"orgId": "acme", "aud": "micromap"})) is not None
    assert auth.verify_workbench_jwt(_tok({"orgId": "acme", "aud": "other"})) is None


def test_issuer_enforced_when_configured(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    monkeypatch.setenv("JWT_ISSUER", "https://workbench")
    assert auth.verify_workbench_jwt(_tok({"orgId": "acme", "iss": "https://workbench"})) is not None
    assert auth.verify_workbench_jwt(_tok({"orgId": "acme", "iss": "evil"})) is None


def test_non_jwt_string_returns_none(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    assert auth.verify_workbench_jwt("not-a-jwt-just-a-static-token") is None


def test_jwks_fetch_failure_returns_none(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("JWT_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("JWKS_URL", "https://workbench/oauth2/jwks")

    class _Boom:
        def __init__(self, *a, **k): ...
        def get_signing_key_from_jwt(self, *a, **k):
            raise RuntimeError("network down")

    monkeypatch.setattr(auth.jwt, "PyJWKClient", _Boom)
    assert auth.verify_workbench_jwt(_tok({"orgId": "acme"})) is None


def test_custom_claim_names(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    monkeypatch.setenv("JWT_ORG_CLAIM", "org_id")
    monkeypatch.setenv("JWT_ROLE_CLAIM", "role")
    claims = auth.verify_workbench_jwt(_tok({"org_id": "acme", "role": "member"}))
    p = auth.principal_from_claims(claims)
    assert p.org_id == "acme" and p.role == "member"


import asyncio


def test_verifier_accepts_jwt(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    v = auth.WorkbenchJWKSVerifier(static_token="static-xyz")
    tok = _tok({"orgId": "acme", "sub": "u1", "roles": ["member"]})
    at = asyncio.run(v.verify_token(tok))
    assert at is not None
    assert at.claims["org_id"] == "acme"
    assert at.claims["auth"] == "jwt"
    assert "member" in at.scopes


def test_verifier_static_fallback(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("JWKS_URL", raising=False)
    monkeypatch.delenv("JWT_PUBLIC_KEY", raising=False)
    v = auth.WorkbenchJWKSVerifier(static_token="static-xyz")
    at = asyncio.run(v.verify_token("static-xyz"))
    assert at is not None and at.claims["auth"] == "static"
    assert asyncio.run(v.verify_token("wrong")) is None


def test_verifier_dual_auth(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    v = auth.WorkbenchJWKSVerifier(static_token="static-xyz")
    assert asyncio.run(v.verify_token(_tok({"orgId": "acme"}))).claims["auth"] == "jwt"
    assert asyncio.run(v.verify_token("static-xyz")).claims["auth"] == "static"


def test_verifier_none_when_no_static_and_not_jwt(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    v = auth.WorkbenchJWKSVerifier(static_token=None)
    assert asyncio.run(v.verify_token("not-a-jwt")) is None


from micromap_mcp.auth import (
    PRIVILEGED_ROLES, default_org, identity_configured, match_static_token,
    org_api_keys, token_orgs,
)


def test_token_orgs_parses_token_org_role(monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok_a:acme:service,tok_b:beta:user")
    assert token_orgs() == {"tok_a": ("acme", "service"), "tok_b": ("beta", "user")}


def test_token_orgs_role_is_optional(monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok_a:acme")
    assert token_orgs() == {"tok_a": ("acme", "")}


def test_token_orgs_skips_malformed_entries_without_raising(monkeypatch):
    # A typo must not take the server down, and must not silently grant an org.
    monkeypatch.setenv("MCP_TOKEN_ORGS", "garbage,:noorg:x,tok_ok:acme:service,tok_bad:")
    assert token_orgs() == {"tok_ok": ("acme", "service")}


def test_token_orgs_empty_when_unset(monkeypatch):
    monkeypatch.delenv("MCP_TOKEN_ORGS", raising=False)
    assert token_orgs() == {}


def test_default_org_mirrors_rest_default(monkeypatch):
    monkeypatch.delenv("MCP_DEFAULT_ORG", raising=False)
    assert default_org() == "default"
    monkeypatch.setenv("MCP_DEFAULT_ORG", "somethingelse")
    assert default_org() == "somethingelse"


def test_default_org_falls_back_when_explicitly_set_empty(monkeypatch):
    # os.environ.get(key, fallback) only falls back when the key is ABSENT --
    # an operator who sets MCP_DEFAULT_ORG="" gets "" back, silently defeating
    # every `... or default_org()` never-empty guarantee in the write paths
    # (Finding 3, #299). Same failure shape as the #269 incident: 139 nodes
    # left with a NULL organization_id, invisible to the org-filtered API.
    monkeypatch.setenv("MCP_DEFAULT_ORG", "")
    assert default_org() == "default"


def test_default_org_falls_back_when_whitespace_only(monkeypatch):
    monkeypatch.setenv("MCP_DEFAULT_ORG", "   ")
    assert default_org() == "default"


def test_principal_org_returns_the_authenticated_principals_org(monkeypatch):
    """#299 final review item 6: the consolidated helper every write path
    (mapforge_plan, provenance_record_finding, provenance_lineage) now calls
    instead of re-deriving `(p.org_id if p else "") or default_org()` inline."""
    from unittest.mock import patch
    with patch("micromap_mcp.auth.current_principal",
               return_value=auth.Principal(org_id="acme", role="user")):
        assert auth.principal_org() == "acme"


def test_principal_org_falls_back_to_default_org_when_unauthenticated(monkeypatch):
    from unittest.mock import patch
    monkeypatch.delenv("MCP_DEFAULT_ORG", raising=False)
    with patch("micromap_mcp.auth.current_principal", return_value=None):
        assert auth.principal_org() == "default"


def test_principal_org_falls_back_when_principal_has_no_org(monkeypatch):
    from unittest.mock import patch
    monkeypatch.delenv("MCP_DEFAULT_ORG", raising=False)
    with patch("micromap_mcp.auth.current_principal",
               return_value=auth.Principal(org_id="")):
        assert auth.principal_org() == "default"


def test_identity_configured_false_when_nothing_set(monkeypatch):
    monkeypatch.delenv("MCP_TOKEN_ORGS", raising=False)
    for k in ("JWT_SECRET", "JWT_PUBLIC_KEY", "JWKS_URL"):
        monkeypatch.delenv(k, raising=False)
    assert identity_configured() is False


def test_identity_configured_true_with_token_map(monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok_a:acme:service")
    assert identity_configured() is True


def test_identity_configured_true_with_jwt_material(monkeypatch):
    monkeypatch.delenv("MCP_TOKEN_ORGS", raising=False)
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    assert identity_configured() is True


def test_match_static_token_returns_mapped_org_and_role(monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok_a:acme:service")
    assert match_static_token("tok_a", None) == ("acme", "service")


def test_match_static_token_accepts_legacy_token_as_default_org(monkeypatch):
    # The legacy single token keeps working, but unmapped means non-privileged.
    monkeypatch.delenv("MCP_TOKEN_ORGS", raising=False)
    assert match_static_token("legacy", "legacy") == ("default", "")


def test_match_static_token_rejects_unknown(monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok_a:acme:service")
    assert match_static_token("nope", "legacy") is None


def test_org_api_keys_parses_org_to_rest_key(monkeypatch):
    monkeypatch.setenv("MCP_ORG_API_KEYS", "acme:rk_1,beta:rk_2")
    assert org_api_keys() == {"acme": "rk_1", "beta": "rk_2"}


def test_privileged_roles_are_service_and_admin():
    assert PRIVILEGED_ROLES == frozenset({"service", "admin"})


def test_static_token_gets_its_mapped_org_and_role(monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok_a:acme:service")
    v = auth.WorkbenchJWKSVerifier(static_token=None)
    at = asyncio.run(v.verify_token("tok_a"))
    assert at is not None
    assert at.claims["org_id"] == "acme"
    assert at.claims["role"] == "service"
    assert at.claims["auth"] == "static"


def test_unmapped_legacy_token_gets_default_org_and_no_role(monkeypatch):
    # The #299 compatibility contract: today's shared token keeps working, but
    # is non-privileged, so enforcement can be switched on without a flag day.
    monkeypatch.delenv("MCP_TOKEN_ORGS", raising=False)
    v = auth.WorkbenchJWKSVerifier(static_token="legacy")
    at = asyncio.run(v.verify_token("legacy"))
    assert at is not None
    assert at.claims["org_id"] == "default"
    assert at.claims["role"] == ""


def test_token_in_map_authenticates_even_without_legacy_token(monkeypatch):
    # Minting per-caller tokens must not require also setting the legacy var.
    monkeypatch.setenv("MCP_TOKEN_ORGS", "tok_b:beta:user")
    v = auth.WorkbenchJWKSVerifier(static_token=None)
    assert asyncio.run(v.verify_token("tok_b")) is not None
    assert asyncio.run(v.verify_token("tok_unknown")) is None
