"""Legacy null-org safety check (#200), made label-agnostic (#269).

The shared+private read predicate
`(n.organization_id = $organization_id OR n.organization_id IN $public_orgs)`
makes any node with a NULL/absent `organization_id` invisible to every caller
(Neo4j treats `null = x` and `null IN [...]` as null, never true). All loaders
stamp `organization_id = "default"` (database/load_knowledge_graph.DEFAULT_ORG_ID),
so there should be none — this integration test pins that. If it fails, backfill
before shipping:

    MATCH (x) WHERE x.organization_id IS NULL SET x.organization_id = 'default'

#269 — why there is no label list here any more:
This test used to iterate a hardcoded LABELS list and so missed the real thing
it existed to catch. The list still named `Metabolite`, which has had 0 nodes
since the #146 `:Metabolite` -> `:Compound` rename, and never named `Gene`. When
139 org-less nodes turned up on kgdev (125 Paper / 12 Compound / 2 Protein), a
per-label check could only ever have found the Paper and Protein halves and
would have reported a clean 0 for Compound forever. Ask the database which
labels exist; don't tell it.

Requires a live, loaded Neo4j; skipped otherwise (mirrors conftest::neo4j_driver).
The same assertion runs against any database (incl. live kgdev) via
`--validate`, backed by `database.validation.validate_null_organization_id`.
"""
import pytest

pytestmark = pytest.mark.integration


def test_no_nodes_with_null_organization_id(neo4j_driver):
    db = getattr(neo4j_driver, "_test_database", "neo4j")
    with neo4j_driver.session(database=db) as s:
        offenders = {
            r["label"]: r["cnt"]
            for r in s.run(
                "MATCH (x) WHERE x.organization_id IS NULL "
                "RETURN labels(x)[0] AS label, count(*) AS cnt ORDER BY cnt DESC"
            )
        }
    assert not offenders, (
        f"Nodes with null organization_id (invisible under the #200 predicate): "
        f"{offenders}. Backfill to 'default' before shipping."
    )
