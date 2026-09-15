"""Async client for the MicroMap REST API. Translates HTTP status into typed
exceptions so callers don't poke at httpx responses."""
from __future__ import annotations
import httpx
from typing import Any


class KGError(Exception):
    """Base class for KG client errors."""

class KGAuthError(KGError):
    """401 from the KG (bad / missing API key)."""

class KGNotFoundError(KGError):
    """404 from the KG (entity not found)."""

class KGRateLimitError(KGError):
    """429 from the KG."""

class KGServerError(KGError):
    """5xx from the KG."""


class KGClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 30.0):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            timeout=timeout_seconds,
        )

    async def get(self, path: str, params: dict[str, Any] | None = None,
                  api_key: str | None = None) -> Any:
        """GET `path`. `api_key` overrides the constructor key for this request
        so a tool can present the CALLER's credential (#299) — REST then derives
        the org itself, and the MCP never asserts an org to REST."""
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"X-API-Key": api_key} if api_key else None
        resp = await self._client.get(path, params=clean, headers=headers)
        s = resp.status_code
        if s == 200:
            return resp.json()
        if s == 401:
            raise KGAuthError(f"401 from {path}: {resp.text[:200]}")
        if s == 404:
            raise KGNotFoundError(f"404 from {path}: {resp.text[:200]}")
        if s == 429:
            raise KGRateLimitError(f"429 from {path}")
        if s >= 500:
            raise KGServerError(f"{s} from {path}: {resp.text[:200]}")
        raise KGError(f"{s} from {path}: {resp.text[:200]}")

    async def aclose(self) -> None:
        await self._client.aclose()
