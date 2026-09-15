import io
import json
import tarfile
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.federation_tenants import FederationTarget


def _approved_bundle_tar(org="acme") -> bytes:
    files = {
        "manifest.json": json.dumps({"files": {}, "approved": True, "reviewer": "alice"}).encode(),
        "routing.yaml": f"destination: micromap-core\norganization_id: {org}\n".encode(),
        "mapping.yaml": ("source:\n  path: d.tsv\n  sha256: " + "a" * 64 + "\n").encode(),
        "resolution.json": json.dumps({"resolved_count": 1, "unresolved_count": 0,
                                       "ambiguous_count": 0, "resolved": []}).encode(),
        "cypher/nodes_Taxon.cypher": (
            "UNWIND $batch_0 AS row MERGE (n:Taxon {ncbi_tax_id: row.ncbi_tax_id, "
            "organization_id: row.organization_id});\n").encode(),
        "cypher/nodes_Taxon.params.json": json.dumps(
            {"batch_0": [{"ncbi_tax_id": "562", "organization_id": org}]}).encode(),
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name); info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def client():
    from api.routes import federation
    app = FastAPI()
    app.include_router(federation.router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def test_missing_key_401(client):
    r = client.post("/api/v1/federation/contributions",
                    files={"bundle": ("b.tgz", _approved_bundle_tar(), "application/gzip")})
    assert r.status_code == 401


def test_unknown_tenant_403(client, monkeypatch):
    monkeypatch.setenv("FEDERATION_TENANTS", "{}")
    r = client.post("/api/v1/federation/contributions",
                    headers={"X-API-Key": "nope"},
                    files={"bundle": ("b.tgz", _approved_bundle_tar(), "application/gzip")})
    assert r.status_code == 403


def test_happy_path_calls_submit_bundle(client, monkeypatch):
    target = FederationTarget(organization_id="acme", database="acme_db",
                              bolt_uri="bolt://x:7687", bolt_auth_ref="env:PW")
    from micromap_mapforge.submit.base import SubmissionReceipt
    receipt = SubmissionReceipt(destination="micromap-core", success=True,
                               nodes_written=1, relationships_written=0)
    with patch("api.routes.federation.resolve_federation_target", return_value=target), \
         patch("api.routes.federation.resolve_bolt_password", return_value="pw"), \
         patch("api.routes.federation.GraphDatabase") as gdb, \
         patch("api.routes.federation.submit_bundle", return_value=receipt) as sb:
        r = client.post("/api/v1/federation/contributions",
                        headers={"X-API-Key": "key_acme"},
                        files={"bundle": ("b.tgz", _approved_bundle_tar(), "application/gzip")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True and body["nodes_written"] == 1
    assert body["database"] == "acme_db"
    _, kwargs = sb.call_args
    assert kwargs["database"] == "acme_db" and kwargs["force"] is False
    gdb.driver.assert_called_once()


def test_unapproved_bundle_400(client, monkeypatch):
    target = FederationTarget(organization_id="acme", database="acme_db",
                              bolt_uri="bolt://x:7687", bolt_auth_ref="env:PW")
    files = {
        "manifest.json": json.dumps({"files": {}, "approved": False}).encode(),
        "routing.yaml": b"destination: micromap-core\norganization_id: acme\n",
        "mapping.yaml": ("source:\n  path: d.tsv\n  sha256: " + "a" * 64 + "\n").encode(),
        "resolution.json": json.dumps({"resolved": []}).encode(),
        "cypher/nodes_Taxon.cypher": b"UNWIND $b AS r MERGE (n:Taxon {id:r.id});\n",
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name); info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    with patch("api.routes.federation.resolve_federation_target", return_value=target):
        r = client.post("/api/v1/federation/contributions",
                        headers={"X-API-Key": "key_acme"},
                        files={"bundle": ("b.tgz", buf.getvalue(), "application/gzip")})
    assert r.status_code == 400
    assert "approved" in r.text.lower()


def test_org_mismatch_400(client):
    target = FederationTarget(organization_id="acme", database="acme_db",
                              bolt_uri="bolt://x:7687", bolt_auth_ref="env:PW")
    with patch("api.routes.federation.resolve_federation_target", return_value=target):
        r = client.post("/api/v1/federation/contributions",
                        headers={"X-API-Key": "key_acme"},
                        files={"bundle": ("b.tgz", _approved_bundle_tar(org="other"), "application/gzip")})
    assert r.status_code == 400
    assert "organization" in r.text.lower()


def test_oversize_bundle_413(client):
    target = FederationTarget(organization_id="acme", database="acme_db",
                              bolt_uri="bolt://x:7687", bolt_auth_ref="env:PW")
    from api.federation_intake import BundleTooLargeError
    with patch("api.routes.federation.resolve_federation_target", return_value=target), \
         patch("api.routes.federation.safe_extract_bundle",
               side_effect=BundleTooLargeError("too big")):
        r = client.post("/api/v1/federation/contributions",
                        headers={"X-API-Key": "key_acme"},
                        files={"bundle": ("b.tgz", _approved_bundle_tar(), "application/gzip")})
    assert r.status_code == 413


def test_disallowed_cypher_400(client):
    """Real validate_bundle_cypher runs — no patch of it."""
    target = FederationTarget(organization_id="acme", database="acme_db",
                              bolt_uri="bolt://x:7687", bolt_auth_ref="env:PW")
    # Build a bundle with a disallowed APOC call in the Cypher file.
    files = {
        "manifest.json": json.dumps({"files": {}, "approved": True, "reviewer": "alice"}).encode(),
        "routing.yaml": b"destination: micromap-core\norganization_id: acme\n",
        "mapping.yaml": ("source:\n  path: d.tsv\n  sha256: " + "a" * 64 + "\n").encode(),
        "resolution.json": json.dumps({"resolved_count": 1, "unresolved_count": 0,
                                       "ambiguous_count": 0, "resolved": []}).encode(),
        "cypher/nodes_Taxon.cypher": (
            "CALL apoc.load.json('http://x') YIELD value RETURN value;\n"
        ).encode(),
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    with patch("api.routes.federation.resolve_federation_target", return_value=target):
        r = client.post("/api/v1/federation/contributions",
                        headers={"X-API-Key": "key_acme"},
                        files={"bundle": ("b.tgz", buf.getvalue(), "application/gzip")})
    assert r.status_code == 400
    assert "cypher" in r.text.lower()


def test_write_failure_502(client):
    """502 path: submit_bundle raises; driver.close() must still be called."""
    target = FederationTarget(organization_id="acme", database="acme_db",
                              bolt_uri="bolt://x:7687", bolt_auth_ref="env:PW")
    with patch("api.routes.federation.resolve_federation_target", return_value=target), \
         patch("api.routes.federation.resolve_bolt_password", return_value="pw"), \
         patch("api.routes.federation.GraphDatabase") as gdb, \
         patch("api.routes.federation.submit_bundle",
               side_effect=RuntimeError("bolt down")) as _sb:
        r = client.post("/api/v1/federation/contributions",
                        headers={"X-API-Key": "key_acme"},
                        files={"bundle": ("b.tgz", _approved_bundle_tar(), "application/gzip")})
    assert r.status_code == 502
    assert "RuntimeError" in r.text
    # The finally block must have closed the driver even though submit_bundle raised.
    assert gdb.driver.return_value.close.called


def test_reviewer_forwarded(client):
    """reviewer form field is forwarded to submit_bundle as a kwarg."""
    target = FederationTarget(organization_id="acme", database="acme_db",
                              bolt_uri="bolt://x:7687", bolt_auth_ref="env:PW")
    from micromap_mapforge.submit.base import SubmissionReceipt
    receipt = SubmissionReceipt(destination="micromap-core", success=True,
                               nodes_written=0, relationships_written=0)
    with patch("api.routes.federation.resolve_federation_target", return_value=target), \
         patch("api.routes.federation.resolve_bolt_password", return_value="pw"), \
         patch("api.routes.federation.GraphDatabase"), \
         patch("api.routes.federation.submit_bundle", return_value=receipt) as sb:
        r = client.post("/api/v1/federation/contributions",
                        headers={"X-API-Key": "key_acme"},
                        files={"bundle": ("b.tgz", _approved_bundle_tar(), "application/gzip")},
                        data={"reviewer": "bob"})
    assert r.status_code == 200, r.text
    _, kwargs = sb.call_args
    assert kwargs["reviewer"] == "bob"


def test_bearer_token_auth(client):
    """Bearer token in Authorization header is accepted in lieu of X-API-Key."""
    target = FederationTarget(organization_id="acme", database="acme_db",
                              bolt_uri="bolt://x:7687", bolt_auth_ref="env:PW")
    from micromap_mapforge.submit.base import SubmissionReceipt
    receipt = SubmissionReceipt(destination="micromap-core", success=True,
                               nodes_written=0, relationships_written=0)
    with patch("api.routes.federation.resolve_federation_target", return_value=target), \
         patch("api.routes.federation.resolve_bolt_password", return_value="pw"), \
         patch("api.routes.federation.GraphDatabase"), \
         patch("api.routes.federation.submit_bundle", return_value=receipt):
        r = client.post("/api/v1/federation/contributions",
                        headers={"Authorization": "Bearer key_acme"},
                        files={"bundle": ("b.tgz", _approved_bundle_tar(), "application/gzip")})
    assert r.status_code == 200, r.text


def test_raw_upload_too_large_413(client, monkeypatch):
    """A raw multipart body exceeding _MAX_UPLOAD_BYTES is rejected with 413."""
    target = FederationTarget(organization_id="acme", database="acme_db",
                              bolt_uri="bolt://x:7687", bolt_auth_ref="env:PW")
    big = b"x" * (100 * 1024 * 1024 + 1)
    with patch("api.routes.federation.resolve_federation_target", return_value=target):
        r = client.post("/api/v1/federation/contributions",
                        headers={"X-API-Key": "key_acme"},
                        files={"bundle": ("b.tgz", big, "application/gzip")})
    assert r.status_code == 413


def _jwt_token(org, secret="fed-secret"):
    import time
    import jwt as _jwt
    return _jwt.encode(
        {"org_id": org, "sub": "u1", "exp": int(time.time()) + 300}, secret, algorithm="HS256"
    )


def test_jwt_derives_tenant_by_org(client, monkeypatch):
    """A valid Workbench JWT selects the registered tenant by its org claim."""
    monkeypatch.setenv("JWT_SECRET", "fed-secret")
    monkeypatch.setenv("FEDERATION_TENANTS", json.dumps({
        "key_acme": {"organization_id": "acme", "database": "acme_db",
                     "bolt_uri": "bolt://x:7687", "bolt_auth_ref": "env:PW"}}))
    from micromap_mapforge.submit.base import SubmissionReceipt
    receipt = SubmissionReceipt(destination="micromap-core", success=True,
                                nodes_written=1, relationships_written=0)
    with patch("api.routes.federation.resolve_bolt_password", return_value="pw"), \
         patch("api.routes.federation.GraphDatabase"), \
         patch("api.routes.federation.submit_bundle", return_value=receipt):
        r = client.post("/api/v1/federation/contributions",
                        headers={"Authorization": f"Bearer {_jwt_token('acme')}"},
                        files={"bundle": ("b.tgz", _approved_bundle_tar(org="acme"), "application/gzip")})
    assert r.status_code == 200, r.text
    assert r.json()["organization_id"] == "acme"


def test_jwt_org_without_registered_tenant_403(client, monkeypatch):
    """A valid JWT whose org is not a registered federation tenant is rejected."""
    monkeypatch.setenv("JWT_SECRET", "fed-secret")
    monkeypatch.setenv("FEDERATION_TENANTS", json.dumps({
        "key_acme": {"organization_id": "acme", "database": "acme_db",
                     "bolt_uri": "bolt://x:7687", "bolt_auth_ref": "env:PW"}}))
    r = client.post("/api/v1/federation/contributions",
                    headers={"Authorization": f"Bearer {_jwt_token('ghost')}"},
                    files={"bundle": ("b.tgz", _approved_bundle_tar(org="ghost"), "application/gzip")})
    assert r.status_code == 403


def test_contribution_meters_gtoken_on_success(client, monkeypatch):
    """On a successful contribution, the org is metered via the g-token client."""
    from api.gtoken import RESOURCE_CONTRIBUTION
    target = FederationTarget(organization_id="acme", database="acme_db",
                              bolt_uri="bolt://x:7687", bolt_auth_ref="env:PW")
    from micromap_mapforge.submit.base import SubmissionReceipt
    receipt = SubmissionReceipt(destination="micromap-core", success=True,
                                nodes_written=1, relationships_written=0)
    seen = {}

    async def fake_deduct(self, org_id, resource_type, **kw):
        seen["org"] = org_id
        seen["resource_type"] = resource_type
        return {"tokensDeducted": 1.0}

    monkeypatch.setenv("GTOKEN_API_URL", "https://wb/auth/api/v3")  # enable metering
    with patch("api.routes.federation.resolve_federation_target", return_value=target), \
         patch("api.routes.federation.resolve_bolt_password", return_value="pw"), \
         patch("api.routes.federation.GraphDatabase"), \
         patch("api.routes.federation.submit_bundle", return_value=receipt), \
         patch("api.gtoken.GTokenClient.deduct", new=fake_deduct):
        r = client.post("/api/v1/federation/contributions",
                        headers={"X-API-Key": "key_acme"},
                        files={"bundle": ("b.tgz", _approved_bundle_tar(org="acme"), "application/gzip")})
    assert r.status_code == 200, r.text
    assert seen == {"org": "acme", "resource_type": RESOURCE_CONTRIBUTION}


def test_contribution_succeeds_even_if_metering_raises(client, monkeypatch):
    """Metering is fail-open: a deduct error never breaks the contribution."""
    target = FederationTarget(organization_id="acme", database="acme_db",
                              bolt_uri="bolt://x:7687", bolt_auth_ref="env:PW")
    from micromap_mapforge.submit.base import SubmissionReceipt
    receipt = SubmissionReceipt(destination="micromap-core", success=True,
                                nodes_written=1, relationships_written=0)

    async def boom_deduct(self, *a, **k):
        raise RuntimeError("meter down")

    monkeypatch.setenv("GTOKEN_API_URL", "https://wb/auth/api/v3")
    with patch("api.routes.federation.resolve_federation_target", return_value=target), \
         patch("api.routes.federation.resolve_bolt_password", return_value="pw"), \
         patch("api.routes.federation.GraphDatabase"), \
         patch("api.routes.federation.submit_bundle", return_value=receipt), \
         patch("api.gtoken.GTokenClient.deduct", new=boom_deduct):
        r = client.post("/api/v1/federation/contributions",
                        headers={"X-API-Key": "key_acme"},
                        files={"bundle": ("b.tgz", _approved_bundle_tar(org="acme"), "application/gzip")})
    assert r.status_code == 200, r.text


def test_route_registered_on_app():
    from api.main import app
    # Use FastAPI's public OpenAPI paths rather than iterating app.routes and
    # touching .path: included sub-routers surface differently across
    # FastAPI/Starlette versions (some app.routes entries have no .path). See #288.
    assert "/api/v1/federation/contributions" in app.openapi()["paths"]
