"""#325: export the caller's decision-log chain for counterparty verification.

Two endpoints, both org-scoped from the API key (never a caller argument), both
returning the org's **whole** chain — never a filtered extract, which would return
the counterparty to trusting a report we produced:

- ``GET /api/v1/provenance/chain/export`` — the whole chain as JSONL, one
  ``{"seq","prev","digest","event"}`` object per line. The chain *head* is the
  digest of the last line; run ``api.provenance.verify_chain`` over the stream to
  recompute it.
- ``GET /api/v1/provenance/chain/head`` — ``{"head","count"}``, the anchor a
  counterparty records now and re-compares against a fresh export later.

The chain is computed at export time from the immutable, ``pk``-ordered rows (#325).
The log is ``DATABASE_URL``-gated; when it is disabled the endpoints answer 503.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from api import decision_log
from api.dependencies import resolve_organization_id
from api.provenance import chain

router = APIRouter()

# Compact, sorted JSON per line — the same canonicalization the digest uses, so the
# stream a counterparty parses is byte-stable.
_LINE = dict(ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_log_enabled() -> None:
    if not decision_log.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="decision log is disabled (no DATABASE_URL) — nothing to export",
        )


@router.get("/provenance/chain/export")
async def export_chain(organization_id: str = Depends(resolve_organization_id)):
    """Stream the caller's whole decision-log chain as JSONL (#325)."""
    _require_log_enabled()

    def _lines():
        for entry in chain.export_org_chain(organization_id):
            yield json.dumps(entry, **_LINE) + "\n"

    return StreamingResponse(
        _lines(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="chain-{organization_id}.jsonl"'},
    )


@router.get("/provenance/chain/head")
async def chain_head(organization_id: str = Depends(resolve_organization_id)):
    """Return the caller's chain head + entry count — the anchor to record and
    re-compare against a fresh export later (a deletion changes the head)."""
    _require_log_enabled()
    head = None
    count = 0
    for entry in chain.export_org_chain(organization_id):
        head = entry["digest"]
        count += 1
    return {"head": head, "count": count}
