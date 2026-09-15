"""
Biomarker signature API routes.

Biomarker signatures are **not available** in the knowledge graph (#305).

This surface was routed but never ingested. Every query rested on the node
label `:BiomarkerSignature`, which is absent from `db.labels()` on kgdev
entirely — as are the two relationship types it used, `PREDICTS`
(signature->disease) and `INCLUDES` (signature->feature taxa). No loader writes
any of the three.

So a node pattern binding `:BiomarkerSignature` could never match, and all
three endpoints answered `200` with an empty result for every request. (That
sentence avoids spelling the Cypher keyword followed by a paren on purpose:
the #200 org-scope guard finds queries by regex over triple-quoted blocks and
would read the example as a real, unscoped query.) That reads as a finding
— "this disease has no biomarker signature above your AUC threshold" — which is
a claim about the biology. The truth is that no signature data exists at all,
so the endpoints now refuse instead. Same reasoning as #304
(`/genes/{id}/taxa`) and the cross-feeding endpoints in `networks.py`.

This is a missing *feature*, not a regression: the former queries ranked
signatures by `auc_roc` and returned weighted feature panels, a coherent design
with no data behind it. They are preserved in git history (the commit
referencing #305) rather than kept as unreachable code that invites someone to
"re-enable" a label that does not exist. Whether to build the ingest or drop
the surface is the open question on #305.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from api.scoping import OrgScope, resolve_org_scope

router = APIRouter()

_UNAVAILABLE = (
    "Biomarker signatures are not available: the :BiomarkerSignature label and "
    "its PREDICTS/INCLUDES relationships are not present in the knowledge "
    "graph, and no loader produces them. See issue #305."
)


@router.get("/biomarkers")
async def list_biomarkers(
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    min_auc: Optional[float] = Query(None, description="Minimum AUC-ROC filter"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Not available: no biomarker signatures exist in the graph.

    Previously returned an empty list with a `200`, indistinguishable from
    "no signature met your filters". See the module docstring and #305.
    """
    raise HTTPException(status_code=501, detail=_UNAVAILABLE)


@router.get("/biomarkers/disease/{disease_id}")
async def get_disease_signatures(
    disease_id: str,
    limit: int = Query(10, ge=1, le=100, description="Maximum number of signatures"),
    min_auc: Optional[float] = Query(None, description="Minimum AUC-ROC filter"),
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Not available: no biomarker signatures exist in the graph.

    Previously returned an empty ranking with a `200`, which reads as "this
    disease has no signature above that AUC". See #305.
    """
    raise HTTPException(status_code=501, detail=_UNAVAILABLE)


@router.get("/biomarkers/{signature_id}")
async def get_biomarker_detail(
    signature_id: str,
    scope: OrgScope = Depends(resolve_org_scope),
):
    """
    Not available: no biomarker signatures exist in the graph.

    Previously 404'd for every `signature_id`, implying the identifier was
    wrong rather than that the surface holds no data at all. See #305.
    """
    raise HTTPException(status_code=501, detail=_UNAVAILABLE)
