"""POST /api/v1/federation/contributions — external self-serve federation (#206).

An external client uploads an approved MapForge bundle as a .tar.gz. The server
authenticates the tenant, unpacks safely, validates (structure, approval, org
match, Cypher allowlist), then runs the shared submit_bundle() core against the
tenant's hub-hosted constituent over Bolt (server-held creds). The client never
opens a Bolt connection.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import yaml
from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile, status
from neo4j import GraphDatabase

from api.federation_cypher_guard import DisallowedCypherError, validate_bundle_cypher
from api.federation_intake import (
    BundleTooLargeError, UnsafeBundleError, safe_extract_bundle,
)
from api import identity
from api.federation_tenants import (
    FederationTarget, UnknownTenantError, resolve_bolt_password,
    resolve_federation_target, resolve_federation_target_by_org,
)
from api.gtoken import RESOURCE_CONTRIBUTION, GTokenClient
from micromap_mapforge.submit.service import (
    BundleManifestMissingError, BundleNotApprovedError, MissingReviewerError,
    MissingSourceShaError, SubmitError, submit_bundle,
)

router = APIRouter()

_REQUIRED = ("routing.yaml", "manifest.json", "mapping.yaml", "resolution.json")
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB raw multipart cap


def _require_target(x_api_key: str | None, authorization: str | None) -> FederationTarget:
    bearer = (
        authorization[len("Bearer "):]
        if authorization and authorization.startswith("Bearer ")
        else None
    )

    # JWT path (GPV-410): a valid Workbench JWT selects the registered tenant by
    # its org claim. Disabled/invalid/not-a-JWT falls through to the api-key path
    # below, so existing static federation keys keep working (no flag-day).
    if bearer and identity.jwt_enabled():
        claims = identity.verify_jwt(bearer)
        if claims is not None:
            org = identity.org_from_claims(claims)
            if org:
                try:
                    return resolve_federation_target_by_org(org)
                except UnknownTenantError:
                    raise HTTPException(
                        status.HTTP_403_FORBIDDEN, "Not a registered federation tenant"
                    ) from None

    key = x_api_key or bearer
    if not key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing API key")
    try:
        return resolve_federation_target(key)
    except UnknownTenantError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a registered federation tenant") from None


async def _meter_contribution(organization_id: str, reference: str | None) -> None:
    """Best-effort g-token deduction for a successful contribution. Never raises
    and never affects the response — metering is gated (no-op unless
    GTOKEN_API_URL is set) and fail-open."""
    try:
        await GTokenClient().deduct(
            organization_id, RESOURCE_CONTRIBUTION, quantity=1, unit="contribution",
            reference=reference,
        )
    except Exception:  # defensive — deduct is already fail-open
        pass


@router.post("/federation/contributions", tags=["Federation"])
async def submit_contribution(
    bundle: UploadFile = File(...),
    reviewer: str | None = Form(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
):
    target = _require_target(x_api_key, authorization)
    data = await bundle.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "upload too large")

    with tempfile.TemporaryDirectory(prefix="fed-contrib-") as tmp:
        bundle_dir = Path(tmp)
        try:
            safe_extract_bundle(data, bundle_dir)
        except BundleTooLargeError as exc:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from exc
        except (UnsafeBundleError, ValueError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"bad bundle: {exc}") from exc

        missing = [f for f in _REQUIRED if not (bundle_dir / f).is_file()]
        if missing or not (bundle_dir / "cypher").is_dir():
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"bundle missing required files: {missing or ['cypher/']}")

        routing = yaml.safe_load((bundle_dir / "routing.yaml").read_text(encoding="utf-8")) or {}
        if routing.get("organization_id") != target.organization_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"bundle organization_id {routing.get('organization_id')!r} does not match "
                f"the tenant's organization {target.organization_id!r}",
            )

        # Reject unapproved bundles early — before touching Bolt credentials.
        manifest = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
        if not manifest.get("approved", False):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "bundle is not approved (manifest.json approved=false)",
            )

        try:
            validate_bundle_cypher(bundle_dir)
        except DisallowedCypherError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"disallowed Cypher: {exc}") from exc

        password = resolve_bolt_password(target.bolt_auth_ref)
        driver = None
        try:
            driver = GraphDatabase.driver(target.bolt_uri, auth=(target.bolt_user, password))
            receipt = submit_bundle(
                bundle_dir, driver=driver, database=target.database,
                reviewer=reviewer, force=False,
            )
        except (BundleNotApprovedError, BundleManifestMissingError, MissingReviewerError,
                MissingSourceShaError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except SubmitError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        except Exception as exc:  # Bolt/driver/write failure
            raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                                f"write to constituent failed: {type(exc).__name__}: {exc}") from exc
        finally:
            if driver is not None:
                driver.close()

        # Meter the contribution against the org's g-token wallet (GPV-410/D).
        # Fail-open + gated: a no-op unless GTOKEN_API_URL is configured, and a
        # metering error never affects the contribution's success.
        await _meter_contribution(target.organization_id, reference=receipt.destination)

    return {
        "destination": receipt.destination,
        "success": receipt.success,
        "nodes_written": receipt.nodes_written,
        "relationships_written": receipt.relationships_written,
        "database": target.database,
        "organization_id": target.organization_id,
    }
