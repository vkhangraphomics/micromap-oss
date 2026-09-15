"""KG read tools. Each tool is a thin shape over a KG REST call; the FastMCP
adapter exposes them as MCP tools. `register_kg_tools` returns a dict of the
raw async callables when `return_callables=True` so tests can hit them without
the transport."""
from __future__ import annotations
from typing import Any, Optional
from urllib.parse import quote
from ..auth import current_principal, org_api_keys
from ..kg_client import KGClient


def register_kg_tools(*, app, client: KGClient, return_callables: bool = False) -> dict:
    """Register all 10 KG tools on `app`. Returns the raw callables when asked."""

    def _caller_key() -> str | None:
        """REST key for the caller's org, or None to use the client default.

        An unmapped org falls back to the client's constructor key; that key's
        own org then governs at the REST layer. Map every org that needs these
        tools — see the deployment note in the #299 spec.
        """
        p = current_principal()
        return org_api_keys().get(p.org_id) if p else None

    def _seg(value: Any) -> str:
        """Percent-encode a caller-supplied value for use as ONE path segment.

        ``safe=""`` matters: without it, ``quote`` leaves ``/`` unescaped, and
        httpx normalizes dot-segments when it builds the request — so an
        unescaped value like ``"../../admin"`` would walk the URL out of
        ``/api/v1/...`` onto an arbitrary REST path this MCP never intended to
        expose, and an unescaped ``?`` would inject query parameters (#299
        final review, Finding 1). Every f-string path below must route its
        caller-supplied component through this function.
        """
        return quote(str(value), safe="")

    async def kg_search(q: str, types: Optional[str] = None,
                        limit: int = 20, offset: int = 0) -> Any:
        """Full-text search across taxa, diseases, and metabolites."""
        return await client.get("/api/v1/search",
                                params={"q": q, "types": types,
                                        "limit": limit, "offset": offset},
                                api_key=_caller_key())

    async def kg_taxon_by_id(taxon_id: str) -> Any:
        """Fetch a taxon by NCBI tax_id or graph taxon_id."""
        return await client.get(f"/api/v1/taxa/{_seg(taxon_id)}", api_key=_caller_key())

    async def kg_taxon_by_name(query: str) -> Any:
        """Search taxa by name (scientific or common)."""
        return await client.get(f"/api/v1/taxa/search/{_seg(query)}", api_key=_caller_key())

    async def kg_taxon_diseases(taxon_id: str, limit: Optional[int] = None) -> Any:
        """Diseases associated with a taxon."""
        return await client.get(f"/api/v1/taxa/{_seg(taxon_id)}/diseases",
                                params={"limit": limit}, api_key=_caller_key())

    async def kg_taxon_metabolites(taxon_id: str, limit: Optional[int] = None) -> Any:
        """Metabolites produced by a taxon."""
        return await client.get(f"/api/v1/taxa/{_seg(taxon_id)}/metabolites",
                                params={"limit": limit}, api_key=_caller_key())

    async def kg_disease_taxa(disease_id: str, direction: Optional[str] = None,
                              limit: Optional[int] = None) -> Any:
        """Taxa associated with a disease (optionally filter by depleted/enriched)."""
        return await client.get(f"/api/v1/diseases/{_seg(disease_id)}/taxa",
                                params={"direction": direction, "limit": limit},
                                api_key=_caller_key())

    async def kg_disease_metabolites(disease_id: str, limit: Optional[int] = None) -> Any:
        """Metabolites associated with a disease."""
        return await client.get(f"/api/v1/diseases/{_seg(disease_id)}/metabolites",
                                params={"limit": limit}, api_key=_caller_key())

    async def kg_metabolite_producers(metabolite_id: str, limit: Optional[int] = None) -> Any:
        """Taxa that produce a metabolite."""
        return await client.get(f"/api/v1/metabolites/{_seg(metabolite_id)}/producers",
                                params={"limit": limit}, api_key=_caller_key())

    async def kg_graph_neighborhood(entity_id: str, max_depth: int = 1, limit: int = 15) -> Any:
        """N-hop neighborhood around an entity. Use entity NAME (e.g.
        'Faecalibacterium prausnitzii'), not bare numeric IDs."""
        return await client.get("/api/v1/graph/neighborhood",
                                params={"entity_id": entity_id, "max_depth": max_depth,
                                        "limit": limit},
                                api_key=_caller_key())

    async def kg_graph_path(from_id: str, to_id: str, max_depth: int = 3) -> Any:
        """Shortest path between two entities. Use names (e.g.
        from_id='Faecalibacterium prausnitzii', to_id='Butyrate')."""
        return await client.get("/api/v1/graph/path",
                                params={"from_id": from_id, "to_id": to_id,
                                        "max_depth": max_depth},
                                api_key=_caller_key())

    callables = {
        "kg_search": kg_search,
        "kg_taxon_by_id": kg_taxon_by_id,
        "kg_taxon_by_name": kg_taxon_by_name,
        "kg_taxon_diseases": kg_taxon_diseases,
        "kg_taxon_metabolites": kg_taxon_metabolites,
        "kg_disease_taxa": kg_disease_taxa,
        "kg_disease_metabolites": kg_disease_metabolites,
        "kg_metabolite_producers": kg_metabolite_producers,
        "kg_graph_neighborhood": kg_graph_neighborhood,
        "kg_graph_path": kg_graph_path,
    }

    if app is not None:
        for name, fn in callables.items():
            app.tool(name=name)(fn)

    if return_callables:
        return callables
    return {}
