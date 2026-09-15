"""Generic Cypher query tools for the federated graphomics knowledge graph.

Replaces the domain-specific kg.py REST tools with three primitives that work
across all constituent databases (micromap, genomics, transcriptomics,
metabolomics, proteomics) and the graphomics composite.
"""
from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from neo4j import AsyncGraphDatabase

from ..auth import PRIVILEGED_ROLES, current_principal, identity_configured


async def _fetch_data(tx: Any, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run ``query`` in a managed async transaction and materialize the rows.

    With the **async** driver, ``tx.run(...)`` returns a coroutine and
    ``AsyncResult.data()`` is itself awaitable, so both must be awaited inside
    the transaction function. The sync-driver idiom ``lambda tx: tx.run(q).data()``
    calls ``.data()`` on an un-awaited coroutine, which raises
    ``'coroutine' object has no attribute 'data'`` — the GPV-400 bug.
    """
    result = await tx.run(query, **(params or {}))
    return await result.data()


def _escape_label(name: str) -> str:
    """Backtick-escape a label/type from the server catalog for safe embedding.

    Names come from ``db.labels()`` / ``db.relationshipTypes()`` — the server
    catalog, never caller input, the same trust basis as the ``WIRED_PREDICATES``
    literals — but escaping keeps a name containing a backtick from breaking the
    query. The *value* returned to the caller is a bound ``$parameter``.
    """
    return name.replace("`", "``")


def _counts_query(names: list[str], match_pat: str, param_prefix: str,
                  constituent: str | None = None) -> str:
    """UNION-ALL of one count-store lookup per catalog name.

    ``match_pat`` is a format string taking the backtick-escaped name, e.g.
    ``"(n:`{}`)"`` or ``"()-[r:`{}`]->()"``. Each branch counts via a count-store
    read (O(1), never a scan), then attaches the name.

    The aggregation MUST come first, alone, in a ``WITH count(...)`` — never
    ``RETURN $name, count(...)``. With a grouping key in the same clause, an
    empty label yields zero input rows → zero groups → **zero output rows**, so
    the tombstone this is meant to surface (`Metabolite`, count 0) would silently
    vanish instead. A bare ``WITH count(...)`` always emits exactly one row.

    For the composite, each branch is wrapped in
    ``CALL {{ USE graphomics.<db> ... }}`` so it routes to the constituent.
    """
    var = "n" if match_pat.startswith("(n") else "r"

    def branch(i: int, name: str) -> str:
        core = (f"MATCH {match_pat.format(_escape_label(name))} "
                f"WITH count({var}) AS count "
                f"RETURN ${param_prefix}{i} AS name, count")
        if constituent:
            return f"CALL {{ USE graphomics.{constituent} {core} }} RETURN name, count"
        return core

    return "\nUNION ALL\n".join(branch(i, n) for i, n in enumerate(names))


async def _counts(session: Any, names: list[str], name_key: str, count_key: str,
                  match_pat: str, param_prefix: str,
                  constituent: str | None) -> list[dict[str, Any]]:
    """Return ``[{name_key: name, count_key: n}]`` for ``names``, count desc.

    A zero-node tombstone (e.g. ``Metabolite`` after the #146 relabel) comes
    back with an explicit ``0`` rather than as a bare name that reads as
    queryable — the silent-empty failure mode this fixes (#320).
    """
    if not names:
        return []
    query = _counts_query(names, match_pat, param_prefix, constituent)
    params = {f"{param_prefix}{i}": n for i, n in enumerate(names)}
    rows = await session.execute_read(lambda tx: _fetch_data(tx, query, params))
    entries = [{name_key: r["name"], count_key: r["count"]} for r in rows]
    entries.sort(key=lambda e: (-e[count_key], e[name_key]))
    return entries


async def _composite_constituents(driver: Any, composite: str) -> list[str]:
    """The composite's constituent database names, from the live catalog (#275).

    Reads ``SHOW DATABASES ... constituents`` on the system database and strips the
    ``<composite>.`` alias prefix, so a constituent added or removed on the server
    is reflected immediately — never a hardcoded list that rots (the federated
    ``primekg`` went missing from schema introspection exactly that way). Sorted;
    deduped across the multiple rows a clustered deployment reports per database.
    """
    prefix = f"{composite}."
    async with driver.session(database="system") as session:
        rows = await session.execute_read(lambda tx: _fetch_data(
            tx,
            "SHOW DATABASES YIELD name, type, constituents "
            "WHERE type = 'composite' RETURN name, constituents",
        ))
    names: set[str] = set()
    for row in rows:
        if row.get("name") != composite:
            continue
        for c in (row.get("constituents") or []):
            names.add(c[len(prefix):] if c.startswith(prefix) else c)
    return sorted(names)


def register_cypher_tools(
    app: FastMCP,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    default_database: str = "graphomics",
    return_callables: bool = False,
) -> dict:

    def _cypher_refusal() -> dict[str, Any] | None:
        """None if raw Cypher is permitted for this caller, else a refusal dict.

        Role-gated rather than parsed: CALL / UNION / USE / subqueries all defeat
        naive predicate injection, and a parser that is almost good enough is
        worse than an honest refusal because it looks like protection (#299).

        Inactive until identity is configured — with one shared token there is
        no way to tell first-party from external, so enforcing would refuse
        every live caller.
        """
        if not identity_configured():
            return None
        p = current_principal()
        role = (p.role if p else "").strip().lower()
        if role in PRIVILEGED_ROLES:
            return None
        return {
            "error": (
                "query_graph requires a service or admin principal. Use the "
                "structured kg_* tools (kg_search, kg_taxon_by_name, "
                "kg_disease_taxa, kg_graph_neighborhood, ...), which are "
                "scoped to your organization, or get_schema / list_databases."
            ),
            "rows": [],
            "count": 0,
        }

    async def query_graph(
        cypher: str,
        database: str = default_database,
    ) -> dict[str, Any]:
        """Execute a read-only Cypher query against any database in the federated graph.

        Use `database="graphomics"` (default) for cross-discipline queries via the
        composite. Use `USE graphomics.<db>` sub-clauses inside the query to route to
        a specific constituent (e.g. `USE graphomics.micromap`).

        Which constituents actually hold data today (#275):
          database="micromap"  — microbe/disease/metabolite/drug/pathway data (the
                                 populated graph)
          database="primekg"   — PrimeKG reference data (small)
        The `genomics`, `transcriptomics`, `metabolomics`, and `proteomics`
        constituents are provisioned but **currently empty** (0 nodes) — a query
        against them returns `{"rows": [], "count": 0}`, which is an empty database,
        NOT a true negative. Call `get_schema` first for live per-label node counts
        before assuming a constituent can answer.

        The query runs in a read transaction — write statements are rejected by the
        server. Returns up to 1000 rows.
        """
        refusal = _cypher_refusal()
        if refusal is not None:
            return refusal

        driver = AsyncGraphDatabase.driver(
            neo4j_uri, auth=(neo4j_user, neo4j_password)
        )
        try:
            async with driver.session(database=database) as session:
                result = await session.execute_read(
                    lambda tx: _fetch_data(tx, cypher)
                )
            return {"rows": result[:1000], "count": len(result)}
        except Exception as exc:
            return {"error": str(exc), "rows": [], "count": 0}
        finally:
            await driver.close()

    async def list_databases() -> dict[str, Any]:
        """List all databases available in the federated graph.

        Returns the composite database and its constituent databases, along with
        their current status. Use the returned names as the `database` parameter
        in `query_graph` or `get_schema`.
        """
        driver = AsyncGraphDatabase.driver(
            neo4j_uri, auth=(neo4j_user, neo4j_password)
        )
        try:
            async with driver.session(database="system") as session:
                result = await session.execute_read(
                    lambda tx: _fetch_data(
                        tx,
                        "SHOW DATABASES YIELD name, currentStatus, type "
                        "WHERE NOT name IN ['system'] "
                        "RETURN name, currentStatus, type "
                        "ORDER BY type DESC, name",
                    )
                )
            return {"databases": result}
        except Exception as exc:
            return {"error": str(exc), "databases": []}
        finally:
            await driver.close()

    async def get_schema(database: str = default_database) -> dict[str, Any]:
        """Return node labels and relationship types for a database, WITH counts.

        Each label is returned as `{"label": name, "nodes": n}` and each
        relationship type as `{"type": name, "count": n}`, sorted by count
        descending. A label the catalog still lists but that holds zero nodes —
        e.g. `Metabolite`, a tombstone of the #146 `Metabolite`→`Compound`
        relabel — comes back with `nodes: 0` rather than as a bare name, so it
        is visibly empty instead of a silent empty result when queried (#320).

        For a constituent database (e.g. "micromap", "genomics"), queries
        `db.labels()` / `db.relationshipTypes()` then the count store directly.

        For the composite database ("graphomics"), returns the same per
        constituent so you know which `USE graphomics.<db>` clause to route to.

        Args:
            database: Name of the database to introspect (default: "graphomics").
        """
        driver = AsyncGraphDatabase.driver(
            neo4j_uri, auth=(neo4j_user, neo4j_password)
        )

        async def _schema_for(session, constituent: str | None) -> dict[str, Any]:
            """labels+types with counts for one database. `constituent` set →
            the introspection routes via `USE graphomics.<db>` on the composite
            session; None → the session is already on the target database."""
            if constituent:
                # The closing brace is a single `}` and lives in a plain (non-f)
                # string segment — where `}}` would stay literal and produce the
                # invalid `... }} RETURN` the live composite canary caught.
                label_q = (f"CALL {{ USE graphomics.{constituent} CALL db.labels() "
                           "YIELD label RETURN label } RETURN label")
                type_q = (f"CALL {{ USE graphomics.{constituent} CALL "
                          "db.relationshipTypes() YIELD relationshipType "
                          "RETURN relationshipType } RETURN relationshipType")
            else:
                label_q = "CALL db.labels() YIELD label RETURN label"
                type_q = ("CALL db.relationshipTypes() YIELD relationshipType "
                          "RETURN relationshipType")
            label_names = [r["label"] for r in await session.execute_read(
                lambda tx, q=label_q: _fetch_data(tx, q))]
            type_names = [r["relationshipType"] for r in await session.execute_read(
                lambda tx, q=type_q: _fetch_data(tx, q))]
            return {
                "labels": await _counts(
                    session, label_names, "label", "nodes", "(n:`{}`)", "l", constituent),
                "relationship_types": await _counts(
                    session, type_names, "type", "count", "()-[r:`{}`]->()", "t", constituent),
            }

        try:
            if database == "graphomics":
                # #275: enumerate the composite's ACTUAL constituents from the
                # server, never a hardcoded list. `primekg` was federated into the
                # composite after the old list was written and went silently
                # missing from schema introspection — the same "hardcoded list
                # rots" failure mode as #269/#298/#320. Ask the DB so it can't.
                constituents = await _composite_constituents(driver, database)
                async with driver.session(database="graphomics") as session:
                    schema = {db: await _schema_for(session, db) for db in constituents}
                return {"database": database, "constituents": schema}
            async with driver.session(database=database) as session:
                out = await _schema_for(session, None)
            return {"database": database, **out}
        except Exception as exc:
            return {"error": str(exc)}
        finally:
            await driver.close()

    callables = {
        "query_graph": query_graph,
        "list_databases": list_databases,
        "get_schema": get_schema,
    }

    if app is not None:
        for name, fn in callables.items():
            app.tool(name=name)(fn)

    if return_callables:
        return callables
    return {}
