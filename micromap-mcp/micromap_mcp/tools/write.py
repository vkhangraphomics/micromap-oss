"""Guarded write surface for the MCP (#268).

query_graph is read-only and MapForge only *adds* — so an agent could observe a
defect in the graph (e.g. 803 ASSOCIATED_WITH_DISEASE edges with zero paper
support) but had no path to correct it. These two tools add a small, bounded
write surface:

- ``graph_set_property`` — a reversible flag/annotate (SET one property).
- ``graph_delete``       — a DELETE of a node/edge (DETACH DELETE for a node).

Design constraints (see the #268 design sign-off):

* **Typed selector, never raw Cypher.** The caller passes a structured
  ``target`` and the tool builds one bounded ``MATCH`` from it, with every
  caller-supplied string (label, key, value, rel type) arriving as a bound
  parameter. There is no string surface for an injected or unbounded delete.
* **Admin-only.** Stricter than query_graph's raw-read gate (service OR admin):
  graph correction is a curation function.
* **Fail-closed.** Refused entirely when identity is not configured — the
  opposite of the read gate's fail-open-during-migration: a write surface must
  not be open while every caller shares one token.
* **Dry-run by default + a hard cap.** ``dry_run=True`` returns the affected
  count and a sample and mutates nothing; a selector matching more than
  ``MAX_AFFECTED`` is refused so a too-broad selector can't slip through.
* **Delete needs an explicit confirm.** ``dry_run=False`` alone previews;
  ``confirm=True`` is required to actually delete (set_property is reversible,
  so it needs no confirm).
* **Provenance by default.** Every applied mutation MERGEs a ``:Decision``
  (``action_type='graph_correction'``), org-stamped with the admin's principal,
  so the correction is attributable and auditable.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastmcp import FastMCP
from neo4j import AsyncGraphDatabase

from ..auth import current_principal, identity_configured, principal_org
from .cypher import _fetch_data

MAX_AFFECTED = 100


def build_match(target: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Build a bounded ``MATCH`` + params from a typed target selector.

    ``{"kind": "node", "label", "key", "value"}`` →
        ``MATCH (n) WHERE $label IN labels(n) AND n[$key] = $value``
    ``{"kind": "relationship", "type", "from": {...}, "to": {...}}`` →
        a single directed edge pinned by its endpoints.

    Every caller string is a bound parameter — the label is matched with
    ``$label IN labels(n)`` (not a ``:Label`` literal) and the rel type with
    ``type(r) = $type`` (not a ``-[:TYPE]-`` literal) — so nothing the caller
    controls reaches the query text.
    """
    kind = target.get("kind")
    if kind == "node":
        match = "MATCH (n) WHERE $label IN labels(n) AND n[$key] = $value"
        params = {"label": target["label"], "key": target["key"],
                  "value": target["value"]}
        return match, params
    if kind == "relationship":
        frm, to = target["from"], target["to"]
        match = (
            "MATCH (a)-[r]->(b) WHERE type(r) = $type "
            "AND $from_label IN labels(a) AND a[$from_key] = $from_value "
            "AND $to_label IN labels(b) AND b[$to_key] = $to_value"
        )
        params = {
            "type": target["type"],
            "from_label": frm["label"], "from_key": frm["key"],
            "from_value": frm["value"],
            "to_label": to["label"], "to_key": to["key"], "to_value": to["value"],
        }
        return match, params
    raise ValueError(
        f"unknown target kind: {kind!r} (expected 'node' or 'relationship')")


def _var(target: dict[str, Any]) -> str:
    return "n" if target.get("kind") == "node" else "r"


def _sample_expr(var: str) -> str:
    """A compact, human-readable preview of what a selector matches (≤5)."""
    if var == "n":
        return "collect(properties(n))[0..5]"
    return ("collect({type: type(r), start: properties(startNode(r)), "
            "end: properties(endNode(r)), properties: properties(r)})[0..5]")


def register_write_tools(
    app: FastMCP | None,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    database: str,
    return_callables: bool = False,
) -> dict:

    def _write_refusal() -> dict[str, Any] | None:
        """None if the caller may write, else a refusal dict.

        Order matters: the fail-closed identity check comes first, so an
        unconfigured deployment refuses even a nominal admin principal.
        """
        if not identity_configured():
            return {"error": (
                "graph write tools are disabled: identity is not configured. "
                "A write surface must not be open while every caller shares one "
                "token — set MCP_TOKEN_ORGS (token:org:role) or JWT to enable.")}
        p = current_principal()
        role = (p.role if p else "").strip().lower()
        if role != "admin":
            return {"error": (
                f"graph write tools require an admin principal (your role is "
                f"{role!r}). Reads (kg_*, query_graph) and MapForge ingest "
                f"remain available.")}
        return None

    async def _preview(session: Any, match: str, params: dict[str, Any],
                       var: str) -> tuple[int, list]:
        query = f"{match} RETURN count(*) AS affected, {_sample_expr(var)} AS sample"
        rows = await session.execute_read(lambda tx: _fetch_data(tx, query, params))
        if not rows:
            return 0, []
        return rows[0].get("affected", 0), rows[0].get("sample", [])

    async def _record_provenance(session: Any, *, outcome: str, target: dict,
                                 affected: int, detail: str) -> str:
        p = current_principal()
        actor = (p.user_id if p else "") or "mcp-admin"
        decision_id = "graph-write-" + uuid.uuid4().hex[:16]
        summary = (f"MCP graph correction: {detail} on {target.get('kind')} "
                   f"affecting {affected} element(s)")
        query = (
            "MERGE (d:Decision {id: $id}) "
            "SET d.tool = 'mcp-graph-write', "
            "d.action_type = 'graph_correction', "
            "d.decision_outcome = $outcome, "
            "d.summary = $summary, "
            "d.affected = $affected, "
            "d.actor_user = $actor, "
            "d.organization_id = $org, "
            "d.occurred_at = datetime() "
            "RETURN d.id AS id"
        )
        await session.execute_write(lambda tx: _fetch_data(tx, query, {
            "id": decision_id, "outcome": outcome, "summary": summary,
            "affected": affected, "actor": actor, "org": principal_org(),
        }))
        return decision_id

    async def graph_delete(
        target: dict[str, Any],
        dry_run: bool = True,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Delete a node or relationship identified by a typed ``target`` selector.

        ``target`` is a structured selector, never Cypher:

          node:  ``{"kind": "node", "label": "Taxon",
                     "key": "taxon_id", "value": "NCBITaxon:999"}``
          edge:  ``{"kind": "relationship", "type": "ASSOCIATED_WITH_DISEASE",
                     "from": {"label": "Taxon", "key": "taxon_id", "value": "…"},
                     "to":   {"label": "Disease", "key": "name_normalized",
                              "value": "parkinson disease"}}``

        A node delete is ``DETACH DELETE`` (its relationships go too). Admin
        only. ``dry_run=True`` (default) returns ``{affected, sample}`` and
        changes nothing. To execute, pass ``dry_run=False`` **and**
        ``confirm=True`` after reviewing the preview. A selector matching more
        than MAX_AFFECTED elements is refused — narrow it. Every applied delete
        writes a ``:Decision`` provenance record.
        """
        refusal = _write_refusal()
        if refusal is not None:
            return refusal
        try:
            match, params = build_match(target)
        except (ValueError, KeyError) as exc:
            return {"error": f"invalid target: {exc}"}
        var = _var(target)

        driver = AsyncGraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
        try:
            async with driver.session(database=database) as session:
                affected, sample = await _preview(session, match, params, var)
                if affected > MAX_AFFECTED:
                    return {"error": (
                        f"selector matches {affected} elements, over the "
                        f"{MAX_AFFECTED} cap — narrow the target."),
                        "affected": affected}
                if dry_run:
                    return {"dry_run": True, "operation": "delete",
                            "affected": affected, "sample": sample}
                if not confirm:
                    return {"error": (
                        "delete requires confirm=True — re-run with confirm "
                        "after reviewing this preview."),
                        "affected": affected, "sample": sample}
                if affected == 0:
                    return {"dry_run": False, "deleted": 0, "affected": 0}

                clause = "DETACH DELETE n" if var == "n" else "DELETE r"
                await session.execute_write(
                    lambda tx: _fetch_data(tx, f"{match} {clause}", params))
                decision_id = await _record_provenance(
                    session, outcome="delete", target=target, affected=affected,
                    detail="delete")
                return {"dry_run": False, "deleted": affected,
                        "provenance_decision_id": decision_id}
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            await driver.close()

    async def graph_set_property(
        target: dict[str, Any],
        prop_key: str,
        prop_value: Any,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Set one property on a node or relationship matched by ``target``.

        The reversible counterpart to ``graph_delete`` — use it to *flag* rather
        than destroy (e.g. mark unsupported edges ``flagged_unsupported=true``
        for a reader-side filter). Same typed selector, same admin gate, same
        cap and dry-run. No ``confirm`` needed because a SET is reversible.
        Every applied set writes a ``:Decision`` provenance record.
        """
        refusal = _write_refusal()
        if refusal is not None:
            return refusal
        try:
            match, params = build_match(target)
        except (ValueError, KeyError) as exc:
            return {"error": f"invalid target: {exc}"}
        var = _var(target)

        driver = AsyncGraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
        try:
            async with driver.session(database=database) as session:
                affected, sample = await _preview(session, match, params, var)
                if affected > MAX_AFFECTED:
                    return {"error": (
                        f"selector matches {affected} elements, over the "
                        f"{MAX_AFFECTED} cap — narrow the target."),
                        "affected": affected}
                if dry_run:
                    return {"dry_run": True, "operation": "set_property",
                            "prop_key": prop_key, "affected": affected,
                            "sample": sample}
                if affected == 0:
                    return {"dry_run": False, "updated": 0, "affected": 0}

                # Native dynamic SET (Neo4j 5.24+): the property NAME is a bound
                # $parameter, so an arbitrary key never touches the query text —
                # the same no-injection basis as the selector, and one form for
                # both node and relationship (no APOC dependency). Verified live:
                # apoc.create.setRelationshipProperty does not exist on the box.
                set_params = {**params, "prop_key": prop_key,
                              "prop_value": prop_value}
                await session.execute_write(lambda tx: _fetch_data(
                    tx, f"{match} SET {var}[$prop_key] = $prop_value "
                        "RETURN count(*)", set_params))
                decision_id = await _record_provenance(
                    session, outcome="set_property", target=target,
                    affected=affected, detail=f"set {prop_key}")
                return {"dry_run": False, "updated": affected,
                        "provenance_decision_id": decision_id}
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            await driver.close()

    callables = {
        "graph_delete": graph_delete,
        "graph_set_property": graph_set_property,
    }
    if app is not None:
        for name, fn in callables.items():
            app.tool(name=name)(fn)
    if return_callables:
        return callables
    return {}
