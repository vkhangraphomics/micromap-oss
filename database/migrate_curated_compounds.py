"""#281: migrate legacy curated compounds onto their canonical HMDB twins.

The curated PRODUCES data (produces_loader) created identifier-less nodes keyed
on `name_lower` — "Butyrate" — which hold all 218,454 producer edges. HMDB's
twin — "Butyric acid" (HMDB0000039) — holds the disease and pathway links. The
two never met, so the graph contained ZERO
`(:Taxon)-[:PRODUCES]->(:Compound)-[:LINKED_TO_DISEASE]->(:Disease)` paths
(#271), which is the whole mechanistic claim the KG exists to make.

#277 loaded the missing twins and their synonyms; #276 built a resolver that
maps a curated name onto the right compound (44 of 47 on live). What remains is
moving the edges — and that cannot be a loader re-run:

    produces_loader.py:1069:  MATCH (m:Compound {name_lower: $metabolite})

The resolver tags the canonical node with the SAME `name_lower`, so while the
legacy node still carries it that MERGE matches BOTH nodes and `--produces`
DOUBLES the edges (218,454 -> ~436K) rather than reconnecting them. Stripping
`name_lower` from the legacy side is the load-bearing step here; everything else
is bookkeeping.

Scope, measured on live (2026-07-15):
  - 47 nodes have `name_lower`, every one with `compound_id IS NULL`
  - 44 hold PRODUCES (218,454 edges); PRODUCES is their ONLY edge type
  - 3 have no edges and no HMDB twin (amuc_1100, "antimicrobial peptides",
    isourolithin a) -> skipped, they stay as they are

Destructive (moves edges, deletes nodes), so it defaults to dry_run=True and is
standalone — never wired into --all.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from database.ingestion.produces_loader import CURATED_COMPOUND_HMDB_IDS

logger = logging.getLogger(__name__)

# Batch size for the edge move. `acetate` alone carries 43,108 edges; a single
# transaction over that on a t3.medium is asking for a heap spike.
_BATCH = 5_000

# Legacy curated nodes and the twin each resolves to.
#
# Scoped to source='curated_produces' deliberately: 12 other NULL-compound_id
# nodes exist (the HMO/Siglec compounds from the 2026-07-14 out-of-band audit
# write, #269). They carry pubchem_cid, no name_lower, and are NOT ours to move.
#
# Resolution mirrors produces_loader._resolve_canonical_compound: prefer a real
# (non-stub) evidence tier, then exact name > iupac > synonym, and require
# uniqueness. Anything ambiguous resolves to NULL here and is reported as
# skipped rather than guessed — see the pins in CURATED_COMPOUND_HMDB_IDS.
_SURVEY = """
MATCH (legacy:Compound)
WHERE legacy.compound_id IS NULL
  AND legacy.name_lower IS NOT NULL
  AND legacy.source = 'curated_produces'
CALL {
    WITH legacy
    MATCH (canon:Compound)
    WHERE canon.compound_id IS NOT NULL
      AND (toLower(canon.name) = legacy.name_lower
           OR toLower(coalesce(canon.iupac_name, '')) = legacy.name_lower
           OR any(s IN coalesce(canon.synonyms, [])
                  WHERE toLower(s) = legacy.name_lower))
    RETURN collect(canon) AS candidates
}
RETURN legacy.name_lower                       AS name_lower,
       elementId(legacy)                       AS legacy_id,
       size([(legacy)<-[:PRODUCES]-() | 1])    AS edges,
       [c IN candidates | c.compound_id]       AS candidate_ids,
       [c IN candidates | c.hmdb_id]           AS candidate_hmdb_ids,
       [c IN candidates | c.name]              AS candidate_names,
       [c IN candidates | c.hmdb_status]       AS candidate_status,
       [c IN candidates | toLower(c.name) = legacy.name_lower] AS candidate_name_match,
       [c IN candidates | toLower(coalesce(c.iupac_name,'')) = legacy.name_lower] AS candidate_iupac_match
ORDER BY edges DESC
"""

# Move producers onto the canonical node, batched. MERGE so a partial/repeated
# run converges instead of duplicating; the source edge is deleted in the same
# batch so the legacy node empties as we go.
_MOVE = """
MATCH (legacy:Compound) WHERE elementId(legacy) = $legacy_id
MATCH (canon:Compound {compound_id: $canonical_id})
MATCH (t:Taxon)-[r:PRODUCES]->(legacy)
CALL {
    WITH t, r, canon
    MERGE (t)-[n:PRODUCES]->(canon)
    ON CREATE SET n.evidence_level = r.evidence_level,
                  n.notes          = r.notes,
                  n.source         = r.source,
                  n.created_at     = r.created_at,
                  n.migrated_from  = 'curated_produces',
                  n.migrated_at    = datetime()
    DELETE r
} IN TRANSACTIONS OF $batch ROWS
"""

_COUNT_MOVED = """
MATCH (canon:Compound {compound_id: $canonical_id})<-[n:PRODUCES]-()
WHERE n.migrated_from = 'curated_produces'
RETURN count(n) AS moved
"""

# Only ever deletes a node that is already edgeless — if the move left anything
# behind, this is a no-op and the run reports it rather than destroying edges.
_DELETE_LEGACY = """
MATCH (legacy:Compound)
WHERE elementId(legacy) = $legacy_id AND NOT (legacy)--()
DETACH DELETE legacy
RETURN count(*) AS deleted
"""

# Tagged by compound_id, never by name: matching on the name would hit the
# legacy twin too, which is the exact fan-out this migration exists to end.
# Safe only AFTER the legacy node is gone.
_TAG_CANONICAL = """
MATCH (canon:Compound {compound_id: $canonical_id})
SET canon.name_lower = $name_lower,
    canon.sources = CASE
        WHEN canon.sources IS NULL THEN ['curated_produces']
        WHEN 'curated_produces' IN canon.sources THEN canon.sources
        ELSE canon.sources + 'curated_produces'
    END,
    canon.updated_at = datetime()
RETURN count(canon) AS tagged
"""

_REAL_EVIDENCE = frozenset({"quantified", "detected"})


def _pick_canonical(row) -> Optional[Dict[str, Any]]:
    """Resolve one legacy row to a single canonical compound, or None.

    Same precedence as produces_loader._resolve_canonical_compound:

      0. an explicit curation decision (CURATED_COMPOUND_HMDB_IDS). Omitting
         this skipped acetate / lactate / vitamin b12 on the first live dry-run
         — 62,928 edges, acetate's 43,108 among them. They are pinned precisely
         because the ladder cannot settle them, so the ladder alone always
         refuses them.
      1. evidence demotes in-silico stubs (HMDB0304356 "formate" [expected]
         loses to "Formic acid" [quantified]) but never promotes a synonym over
         a real name match ('vitamin k2' stays vitamin K2, not Menadione = K3).
      2. exact name > iupac > synonym, uniqueness required.

    Ambiguity returns None -> reported as skipped, never guessed.
    """
    ids = row["candidate_ids"]
    if not ids:
        return None

    hmdb_ids = row["candidate_hmdb_ids"]
    cands = [
        {
            "compound_id": ids[i],
            "hmdb_id": hmdb_ids[i],
            "name": row["candidate_names"][i],
            "status": row["candidate_status"][i],
            "name_match": row["candidate_name_match"][i],
            "iupac_match": row["candidate_iupac_match"][i],
        }
        for i in range(len(ids))
    ]

    pinned = CURATED_COMPOUND_HMDB_IDS.get(row["name_lower"])
    if pinned:
        for c in cands:
            if c["hmdb_id"] == pinned:
                return c
        logger.warning(
            "Curated metabolite %r is pinned to %s but no candidate has that "
            "hmdb_id (%s) — skipping rather than guessing.",
            row["name_lower"], pinned, [c["hmdb_id"] for c in cands],
        )
        return None

    if any((c["status"] or "").strip().lower() in _REAL_EVIDENCE for c in cands):
        cands = [
            c for c in cands
            if (c["status"] or "").strip().lower() in _REAL_EVIDENCE
        ]

    for tier in ("name_match", "iupac_match", None):
        hits = [c for c in cands if c[tier]] if tier else cands
        if not hits:
            continue
        if len(hits) == 1:
            return hits[0]
        return None  # ambiguous at the winning tier -> refuse
    return None


def migrate(driver, database: str = "neo4j", dry_run: bool = True) -> Dict[str, Any]:
    """Move curated PRODUCES onto canonical compounds and retire the legacy nodes.

    Defaults to dry_run=True: this deletes nodes and rewrites 218K edges, and
    destructive-by-omission is how accidents happen.

    Returns a report. Idempotent — a second run finds nothing left to do.
    """
    planned, skipped = [], []

    with driver.session(database=database) as session:
        for row in session.run(_SURVEY):
            canon = _pick_canonical(row)
            if not canon:
                skipped.append(row["name_lower"])
                continue
            planned.append({
                "name_lower": row["name_lower"],
                "legacy_id": row["legacy_id"],
                "edges": row["edges"],
                "canonical_id": canon["compound_id"],
                "canonical_name": canon["name"],
            })

    total = sum(p["edges"] for p in planned)

    if dry_run:
        logger.info(
            "DRY RUN: would move %d PRODUCES edge(s) from %d legacy compound(s); "
            "skipping %d unresolvable (%s)",
            total, len(planned), len(skipped), ", ".join(skipped) or "none",
        )
        return {
            "dry_run": True,
            "planned": planned,
            "skipped": skipped,
            "total_edges_to_move": total,
        }

    migrated, moved_total, deleted_total = [], 0, 0

    with driver.session(database=database) as session:
        for p in planned:
            # Order matters: move, then delete the emptied node, then tag the
            # canonical. Tagging before the delete would put name_lower on two
            # nodes at once — the fan-out we are here to remove.
            session.run(
                _MOVE,
                legacy_id=p["legacy_id"],
                canonical_id=p["canonical_id"],
                batch=_BATCH,
            ).consume()

            moved = session.run(
                _COUNT_MOVED, canonical_id=p["canonical_id"]
            ).single()["moved"]

            deleted = session.run(_DELETE_LEGACY, legacy_id=p["legacy_id"]).single()
            deleted = deleted["deleted"] if deleted else 0
            if not deleted:
                logger.warning(
                    "Legacy compound %r still has relationships after the move; "
                    "left in place. name_lower is still on it, so --produces "
                    "would still fan out for this name — investigate.",
                    p["name_lower"],
                )

            session.run(
                _TAG_CANONICAL,
                canonical_id=p["canonical_id"],
                name_lower=p["name_lower"],
            ).consume()

            moved_total += moved
            deleted_total += deleted
            migrated.append({**p, "moved": moved, "legacy_deleted": bool(deleted)})
            logger.info(
                "Migrated %r -> %s (%s): %d edge(s) moved, legacy deleted=%s",
                p["name_lower"], p["canonical_name"], p["canonical_id"],
                moved, bool(deleted),
            )

    logger.info(
        "Migration complete: %d compound(s), %d edge(s) moved, %d legacy node(s) "
        "deleted, %d skipped", len(migrated), moved_total, deleted_total, len(skipped),
    )
    return {
        "dry_run": False,
        "migrated": migrated,
        "skipped": skipped,
        "total_edges_moved": moved_total,
        "legacy_deleted": deleted_total,
    }
