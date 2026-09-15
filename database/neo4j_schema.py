"""Provenance-spine schema for the Graphomics Knowledge Graph.

This module used to also hold `GraphomicsSchema` / `GraphomicsSchemaManager`, a
second schema definition that declared uniqueness constraints for every core
entity. It was removed in #272: nothing ever called it except this file's own
`__main__` block, so it had never run — and that block defaulted to the Fabric
composite `graphomics`, where constraints cannot be created anyway. Its list had
also rotted (`metabolite_id_unique` on `:Metabolite`, a label with 0 nodes since
the #146 rename), so it would not have constrained `:Compound` even if invoked.

Two rival schema definitions, one of them dead and stale, is how kgdev ended up
with exactly 3 uniqueness constraints — the ones below — while Taxon, Compound,
Disease, Paper, Drug, Pathway, Gene and Protein had none.

The single source of truth for entity constraints and indexes is now
`load_knowledge_graph.create_indexes_and_constraints` (the `--indexes` path,
which is what production actually runs). This module keeps only the provenance
spine, whose statements are the reason those 3 constraints existed at all.
"""

import logging

logger = logging.getLogger(__name__)


def create_provenance_spine_schema(session) -> dict:
    """GPV-254 provenance spine: Experiment/Analysis/Assertion constraints + indexes.

    Returns a count of constraints created/failed so the caller can surface them.
    Constraint failures are logged at ERROR, not warning: the usual cause is
    pre-existing duplicates, and a swallowed failure leaves the label
    unconstrained while the log implies otherwise (#272's lesson, #311).
    """
    statements = [
        "CREATE CONSTRAINT experiment_id IF NOT EXISTS FOR (e:Experiment) REQUIRE e.id IS UNIQUE",
        "CREATE CONSTRAINT analysis_id IF NOT EXISTS FOR (a:Analysis) REQUIRE a.id IS UNIQUE",
        "CREATE CONSTRAINT assertion_id IF NOT EXISTS FOR (a:Assertion) REQUIRE a.id IS UNIQUE",
        # #311. Bare `id`, matching the #257 siblings above and — decisively —
        # matching what both writers MERGE on: `MERGE (d:Decision {id: $id})` in
        # api/routes/provenance_decisions.py and in
        # micromap_mapforge.provenance.decision. A composite
        # (organization_id, id) would NOT be the merge key: ingesting org B's
        # decision would MATCH org A's node by id and overwrite its
        # organization_id, raising nothing. Decision ids are globally unique by
        # construction (one per contribution), so bare `id` is the real key.
        #
        # Verified on kgdev `micromap` 2026-07-26 before shipping: 75 :Decision
        # nodes, 75 distinct ids, 0 duplicated, 0 null — so this is created
        # cleanly there and no dedupe is needed. If it ever DOES fail, the ERROR
        # below names the constraint; find the offenders and merge them by hand:
        #
        #   MATCH (d:Decision) WITH d.id AS id, collect(d) AS ds, count(*) AS n
        #   WHERE n > 1 RETURN id, n ORDER BY n DESC
        #
        # Keep the earliest `occurred_at` of each group, re-point its edges, and
        # delete the rest — there is no automatic dedupe here on purpose, since
        # which duplicate is canonical is a judgement call.
        "CREATE CONSTRAINT decision_id IF NOT EXISTS FOR (d:Decision) REQUIRE d.id IS UNIQUE",
        "CREATE INDEX assertion_predicate IF NOT EXISTS FOR (a:Assertion) ON (a.predicate)",
        "CREATE INDEX assertion_valid_from IF NOT EXISTS FOR (a:Assertion) ON (a.valid_from)",
        "CREATE INDEX assertion_valid_to IF NOT EXISTS FOR (a:Assertion) ON (a.valid_to)",
        "CREATE INDEX assertion_org IF NOT EXISTS FOR (a:Assertion) ON (a.organization_id)",
        # Serves static `t.ncbi_tax_id = $x` equality lookups elsewhere in the
        # API (taxa/networks/papers routes). NOTE: the spine's own endpoint
        # resolution uses a DYNAMIC property key (`n[$key]`, see _endpoint_match
        # in spine.py), which Neo4j cannot serve from any property index — that
        # path is a label scan regardless. This index does not accelerate it.
        "CREATE INDEX taxon_ncbi_tax_id IF NOT EXISTS FOR (t:Taxon) ON (t.ncbi_tax_id)",
    ]
    created = failed = 0
    for stmt in statements:
        is_constraint = stmt.startswith("CREATE CONSTRAINT")
        try:
            session.run(stmt).consume()
            if is_constraint:
                created += 1
            logger.debug(f"Executed provenance spine schema statement: {stmt}")
        except Exception as e:
            if is_constraint:
                failed += 1
                # Deliberately not logger.exception: a uniqueness violation is
                # self-explanatory and the traceback is noise — but it must be
                # loud, because the alternative is an unconstrained label that
                # the log claims is constrained.
                logger.error(  # noqa: TRY400
                    "Constraint creation FAILED: %s -- %s. Usually means "
                    "duplicates already exist on that key; fix the data, then "
                    "re-run --indexes.",
                    stmt.split(" IF NOT EXISTS")[0], e,
                )
            else:
                logger.warning(f"Failed to execute provenance spine schema statement '{stmt}': {e}")
    return {"constraints_created": created, "constraints_failed": failed}


# The former `CYPHER_QUERIES` dict + `get_query()` accessor lived here.
# 11 of 12 entries had zero callers across the entire repo; the one live
# entry (`get_best_signatures_for_disease`) had a single caller in
# `api/routes/biomarkers.py`, which now carries the query inline. The
# rest of the codebase consolidated on per-route / per-loader inline
# Cypher long ago — see the chore PR that retired this indirection
# layer for the caller-grep / dead-key analysis.
