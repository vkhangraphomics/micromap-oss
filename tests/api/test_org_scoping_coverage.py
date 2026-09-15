"""Coverage guard (#200): every data-route Cypher query must be org-scoped.

Fails CI if a route module contains a query that MATCHes nodes without applying
the org-scope predicate — catches a missed query during the rollout and any
future regression. The data routes derive the caller's org from their API key
and AND `ORG_FILTER(var)` into each query; this test asserts that wiring is
present in every query literal.

A query is considered scoped if its literal contains any of:
- `organization_id`            — the predicate is inlined directly, or
- `ORG_FILTER`                 — the predicate is injected via the helper in an
                                 f-string / query-builder function, or
- `{where`                     — the predicate is in an interpolated WHERE clause
                                 built by appending `ORG_FILTER(...)`.

If you add a genuinely org-agnostic query (e.g. `CALL db.labels()`), add a short
substring of it to ALLOWLIST with a comment explaining why it needs no scope.
"""
import re
from pathlib import Path

import pytest

ROUTE_FILES = [
    "taxa", "diseases", "metabolites", "pathways", "search", "drugs",
    "proteins", "genes", "biomarkers", "papers", "networks", "discovery", "graph",
]

# Tokens that prove a query literal carries the org-scope predicate, whether
# inlined or injected via an interpolated clause (`{where_clause}`, `{org}`).
SCOPED_TOKENS = ("organization_id", "ORG_FILTER", "{where", "{org")

# Query-literal substrings that are intentionally NOT org-scoped, with reason.
ALLOWLIST: set[str] = {
    # graph.py shortestPath/allShortestPaths: anchors are matched by internal id
    # (`id(a) = $from_neo_id`) where those ids come ONLY from the org-scoped
    # _find_entity_query() lookups — so the endpoints are scoped by construction.
    # (Intermediate path nodes are an accepted, documented residual exposure.)
    "id(a) = $from_neo_id",
}


def _query_literals(src: str):
    """All triple-quoted string literals that contain a Cypher MATCH clause.

    Matches both plain and f-string triple-quoted blocks (the `f` prefix sits
    outside the quotes, so the captured body is identical)."""
    return [q for q in re.findall(r'"""(.*?)"""', src, re.S) if "MATCH (" in q]


@pytest.mark.parametrize("name", ROUTE_FILES)
def test_every_route_query_is_org_scoped(name):
    src = Path(f"api/routes/{name}.py").read_text(encoding="utf-8")
    offenders = [
        q.strip()[:100]
        for q in _query_literals(src)
        if not any(tok in q for tok in SCOPED_TOKENS)
        and not any(a in q for a in ALLOWLIST)
    ]
    assert not offenders, (
        f"{name}.py has unscoped queries (no org predicate):\n  - "
        + "\n  - ".join(offenders)
    )


# #297: contributed-edge scoping. Files whose structured reads traverse the two
# edge types that can carry a per-org contribution. A query that BINDS an edge
# var of these types (`[r:ASSOCIATED_WITH_DISEASE]`/`[r:PRODUCES]`) must scope
# that var — else an org-stamped edge between two shared (`default`) nodes leaks
# to every caller (the #284 leak in edge form). Enforced only where the edge var
# is bound; an unbound existence/aggregate traversal is out of scope (it reads no
# per-edge org and the endpoint nodes are already node-scoped).
EDGE_SCOPE_FILES = ["diseases", "taxa", "metabolites", "discovery", "networks"]
_BOUND_CONTRIB_EDGE = re.compile(r"\[(\w+):(?:ASSOCIATED_WITH_DISEASE|PRODUCES)\]")


@pytest.mark.parametrize("name", EDGE_SCOPE_FILES)
def test_bound_contributed_edges_are_org_scoped(name):
    src = Path(f"api/routes/{name}.py").read_text(encoding="utf-8")
    offenders = []
    for q in _query_literals(src):
        for var in _BOUND_CONTRIB_EDGE.findall(q):
            scoped = (
                # inline in this literal: AND {EDGE_ORG_FILTER("r")} or a raw predicate
                f'EDGE_ORG_FILTER("{var}")' in q
                or f"{var}.organization_id" in q
                # injected via an interpolated WHERE built from a clause list/string
                # that the module wires the edge filter into (same pragmatism the
                # node guard applies to `{where`, tightened: the module must
                # actually reference EDGE_ORG_FILTER to earn the pass).
                or ("{where" in q and "EDGE_ORG_FILTER" in src)
            )
            if not scoped:
                offenders.append(f"[{var}:...] in: {q.strip()[:90]}")
    assert not offenders, (
        f"{name}.py binds a contributed edge without edge-org scoping "
        f"(add AND {{EDGE_ORG_FILTER(\"r\")}}):\n  - " + "\n  - ".join(offenders)
    )


@pytest.mark.parametrize("name", ROUTE_FILES)
def test_every_route_wires_resolve_org_scope(name):
    """Each data-route module must import the scope dependency — proves the
    endpoints can derive the caller's org rather than running unscoped."""
    src = Path(f"api/routes/{name}.py").read_text(encoding="utf-8")
    assert "resolve_org_scope" in src, f"{name}.py does not import/use resolve_org_scope"

    # A module that issues no query cannot leak across orgs, so it has nothing
    # to pass scope params to. `networks`/`biomarkers` became such modules in
    # #305: their data does not exist in the graph, so every endpoint returns
    # 501 instead of running Cypher.
    #
    # Derived from the source, deliberately — NOT a list of exempt module names.
    # A name list would silently keep exempting a module that later regained a
    # query, which is precisely the rot that #269/#298 were about. If a query
    # comes back, so does this requirement.
    if not _query_literals(src):
        return

    assert "scope_params" in src, f"{name}.py does not use scope_params to pass org params"
