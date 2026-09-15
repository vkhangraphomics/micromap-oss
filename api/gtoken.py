"""Workbench g-token deduction client (GPV-410 / #232 work item D).

Meters MicroMap usage against the caller's org wallet in Workbench. Built to
Workbench's actual ``DeductTokensRequest`` contract — **camelCase, with
``orgId`` in the body** (the deduct endpoint is internal/unauthenticated and
reads ``orgId`` from the body, not a header). This intentionally differs from
Nexus's ``integrations/gtoken_client.py``, whose snake_case + ``X-Org-Id``
payload is drifted from this DTO; reconcile separately.

Fail-open by design:
- ``disabled`` (no ``GTOKEN_API_URL``) → every call is a no-op (``None``).
- Any HTTP/network error, a 402 (insufficient funds), or a non-numeric org
  → ``None``. The client NEVER raises, so metering can never break a request.

Activation (gated, deferred): set ``GTOKEN_API_URL=<workbench>/auth/api/v3``,
register the rate in Workbench, and ensure the org passed in is the Workbench
numeric ``orgId`` (a slug like "acme" is skipped). ``WORKBENCH_AUTH_TOKEN`` is
sent as a Bearer if set (the deduct endpoint is internal, but a token may be
needed at the ingress).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Resource type for a MapForge contribution write (the rate must be registered
# in Workbench under this name before metering has any effect).
RESOURCE_CONTRIBUTION = "micromap_contribution"

_DEDUCT_PATH = "/internal/tokens/deduct"


class GTokenClient:
    """Async client for Workbench's g-token deduction endpoint. Never raises."""

    def __init__(self, base_url: Optional[str] = None, auth_token: Optional[str] = None) -> None:
        raw = base_url if base_url is not None else os.environ.get("GTOKEN_API_URL", "")
        self.base_url = (raw or "").rstrip("/")
        self.auth_token = (
            auth_token if auth_token is not None else os.environ.get("WORKBENCH_AUTH_TOKEN", "")
        )

    @property
    def disabled(self) -> bool:
        """True when no base URL is configured — metering is a no-op."""
        return not self.base_url

    async def deduct(
        self,
        org_id: Any,
        resource_type: str,
        quantity: float = 1.0,
        unit: str = "contribution",
        reference: Optional[str] = None,
    ) -> Optional[dict]:
        """Deduct ``quantity`` units of ``resource_type`` from ``org_id``'s wallet.

        Returns the response dict, or ``None`` on disabled / non-numeric org /
        402 / any error.
        """
        if self.disabled:
            return None
        try:
            oid = int(org_id)
        except (TypeError, ValueError):
            logger.warning(
                "gtoken deduct skipped: org %r is not a numeric Workbench orgId", org_id
            )
            return None

        payload: dict = {
            "orgId": oid,
            "resourceType": resource_type,
            "quantity": float(quantity),
            "unit": unit,
        }
        if reference:
            payload["reference"] = reference
        headers = {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}

        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
                resp = await client.post(_DEDUCT_PATH, json=payload, headers=headers)
                if resp.status_code == 402:
                    logger.info("gtoken: insufficient funds for org %s (%s)", oid, resource_type)
                    return None
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:  # network / HTTP / decode — fail open
            logger.warning("gtoken deduct failed for org %s: %s", oid, exc)
            return None
