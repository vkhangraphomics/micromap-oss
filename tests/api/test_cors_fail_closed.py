"""CORS must fail closed (#324).

Live prod was verified (2026-08-25) reflecting arbitrary Origins with
`access-control-allow-credentials: true` — the wildcard default in
`api/main.py` plus `allow_credentials=True` turns into the reflected-origin
variant, which browsers treat as "send credentials cross-origin to anyone".

The contract under test:
- No `CORS_ORIGINS` set → no cross-origin access at all (not wildcard).
- An explicit `*` is dropped, so a stale `.env` on the box cannot reopen it.
- When an allowlist IS set: no credentials, narrowed methods and headers.
- No deploy config ships a wildcard.
"""

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: api.main reads CORS_ORIGINS at import time, so the fail-closed default can
#: only be observed when the pytest process itself has it unset.
_env_has_cors = pytest.mark.skipif(
    bool(os.environ.get("CORS_ORIGINS")),
    reason="CORS_ORIGINS set in this environment; import-time default not observable",
)


# ---------------------------------------------------------------------------
# The live app: unset CORS_ORIGINS must mean NO cross-origin access
# ---------------------------------------------------------------------------

@_env_has_cors
def test_no_cors_headers_on_response_when_unset():
    from api.main import app

    client = TestClient(app)
    response = client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


@_env_has_cors
def test_no_cors_preflight_approval_when_unset():
    from api.main import app

    client = TestClient(app)
    response = client.options(
        "/health",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in response.headers


# ---------------------------------------------------------------------------
# parse_cors_origins: the fail-closed parser
# ---------------------------------------------------------------------------

def test_parse_none_is_empty():
    from api.main import parse_cors_origins

    assert parse_cors_origins(None) == []


def test_parse_empty_string_is_empty():
    from api.main import parse_cors_origins

    assert parse_cors_origins("") == []


def test_parse_wildcard_is_dropped():
    # A stale box .env still says CORS_ORIGINS=* (terraform wrote it); the
    # parser dropping it means deploying this code closes the hole even
    # before the box config is corrected.
    from api.main import parse_cors_origins

    assert parse_cors_origins("*") == []


def test_parse_allowlist_strips_whitespace_and_empties():
    from api.main import parse_cors_origins

    value = " https://a.example.com , https://b.example.com ,"
    assert parse_cors_origins(value) == [
        "https://a.example.com",
        "https://b.example.com",
    ]


# ---------------------------------------------------------------------------
# configure_cors: allowlisted behaviour — no credentials, narrowed surface
# ---------------------------------------------------------------------------

def _configured_app(env_value: str) -> TestClient:
    from api.main import configure_cors

    test_app = FastAPI()

    @test_app.get("/ping")
    async def ping():
        return {}

    configure_cors(test_app, env_value)
    return TestClient(test_app)


def test_allowlisted_origin_allowed_without_credentials():
    client = _configured_app("https://app.graphomics.com")
    response = client.get("/ping", headers={"Origin": "https://app.graphomics.com"})
    assert (
        response.headers["access-control-allow-origin"]
        == "https://app.graphomics.com"
    )
    assert "access-control-allow-credentials" not in response.headers


def test_unlisted_origin_refused():
    client = _configured_app("https://app.graphomics.com")
    response = client.get("/ping", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in response.headers


def test_preflight_refuses_unused_methods():
    # The API surface is GET/POST only — DELETE preflight must not be approved.
    client = _configured_app("https://app.graphomics.com")
    response = client.options(
        "/ping",
        headers={
            "Origin": "https://app.graphomics.com",
            "Access-Control-Request-Method": "DELETE",
        },
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Deploy config: no checked-in wildcard anywhere (compose, terraform)
# ---------------------------------------------------------------------------

DEPLOY_CONFIGS = [
    "docker-compose.yml",
    "docker-compose.prod.yml",
    "federation/docker-compose.acme.yml",
    "micromap-mapforge/docker-compose.federation-test.yml",
    "terraform/user_data.sh",
]


@pytest.mark.parametrize("rel_path", DEPLOY_CONFIGS)
def test_no_wildcard_cors_in_deploy_config(rel_path):
    text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    offending = [
        line.strip()
        for line in text.splitlines()
        if "CORS_ORIGINS" in line and "*" in line
    ]
    assert not offending, (
        f"{rel_path} ships a wildcard CORS default: {offending} — "
        "fail closed instead (#324)"
    )
