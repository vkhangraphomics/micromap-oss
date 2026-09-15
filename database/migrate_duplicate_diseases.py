"""#267: merge duplicate :Disease node pairs onto one canonical node.

The compound half of #267 was resolved by #277/#276/#281. The disease half is
still live: each of several diseases exists as **two nodes with distinct
`name_normalized`** — an apostrophe/possessive/plural variant — that hold
complementary halves of the chain. Verified live 2026-08-25 (`micromap`):

    Crohn Disease   (crohn disease)   1356 taxa   0 compounds   0 pathways
    Crohn's Disease (crohns disease)   243 taxa  48 compounds  86 pathways
    Parkinson Disease   (parkinson disease)  1123 taxa  0 compounds
    Parkinson's Disease (parkinsons disease)   4 taxa  7 compounds

The genera-rich twin carries the microbe associations; the possessive twin
carries the metabolite/pathway links. Merging them completes the
`taxon → ASSOCIATED_WITH_DISEASE → disease ← LINKED_TO_DISEASE ← compound`
convergence for these diseases.

Approach mirrors #281 (`migrate_curated_compounds`): survey → pick canonical →
dry-run report → apply. Duplicates are discovered by a collapse key that ignores
apostrophes and possessive/plural `s`; a group is only acted on when it holds
>1 distinct disease. The genera-richest twin is canonical (per the #267
decision); the others fold into it via `apoc.refactor.mergeNodes` (which moves
and de-duplicates every relationship), and their surface names + normalized
values are preserved as `aliases`.

Destructive (moves edges, deletes the folded nodes), so it defaults to
dry_run=True and is standalone — never wired into --all.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


#: One row per disease, with its associated-taxon count (the canonical tiebreak).
_SURVEY = """
MATCH (d:Disease)
OPTIONAL MATCH (t:Taxon)-[:ASSOCIATED_WITH_DISEASE]->(d)
RETURN d.name_normalized AS norm, d.name AS name, d.disease_id AS id,
       elementId(d) AS eid, count(DISTINCT t) AS taxa
"""

#: Fold the duplicates into the canonical via APOC. `mergeRels:true` moves AND
#: de-duplicates relationships (so the taxa the twins share collapse to one
#: edge, not two); `properties:'discard'` keeps the canonical's own properties
#: (name/name_normalized/disease_id), and the folded names are captured as
#: aliases BEFORE the merge (the duplicate nodes are deleted by mergeNodes).
_MERGE = """
MATCH (canon:Disease) WHERE elementId(canon) = $canon_eid
MATCH (dup:Disease) WHERE elementId(dup) IN $dup_eids
WITH canon, collect(dup) AS dups
CALL apoc.refactor.mergeNodes([canon] + dups, {mergeRels: true, properties: 'discard'})
    YIELD node
SET node.aliases = coalesce(node.aliases, []) + $aliases,
    node.merged_from = coalesce(node.merged_from, []) + $merged_norms,
    node.merged_at = datetime()
RETURN elementId(node) AS survivor
"""


def collapse_key(name_normalized: str) -> str:
    """Key that ignores apostrophes and a trailing possessive/plural `s`.

    'crohn disease' / 'crohns disease' -> 'crohn disease';
    'kidney disease' / 'kidney diseases' -> 'kidney disease';
    'parkinsonian syndrome' stays distinct from 'parkinson disease'.

    Used ONLY to group merge candidates — every group is then verified to hold
    more than one distinct disease before anything is touched, so an accidental
    collision surfaces in the dry-run rather than silently merging.
    """
    s = (name_normalized or "").lower().replace("'", "").replace("’", "")
    words = []
    for w in s.split():
        if len(w) > 3 and w.endswith("s"):
            w = w[:-1]
        words.append(w)
    return " ".join(words)


def _plan_from_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group diseases by collapse_key and, for each real duplicate group, pick
    the genera-richest as canonical and the rest as duplicates to fold in."""
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("norm"):
            groups[collapse_key(r["norm"])].append(dict(r))

    plan: List[Dict[str, Any]] = []
    for key, members in groups.items():
        distinct_norms = {m["norm"] for m in members}
        if len(distinct_norms) < 2:
            continue
        # Canonical = most associated taxa; ties broken by name_normalized for
        # determinism. The #267 decision: fold the sparse twin into the rich one.
        members.sort(key=lambda m: (-(m["taxa"] or 0), m["norm"]))
        canonical, duplicates = members[0], members[1:]
        plan.append({
            "collapse_key": key,
            "canonical_norm": canonical["norm"],
            "canonical_name": canonical["name"],
            "canonical_eid": canonical["eid"],
            "canonical_taxa": canonical["taxa"],
            "duplicates": duplicates,
        })
    plan.sort(key=lambda p: p["collapse_key"])
    return plan


def migrate(driver, database: str = "neo4j", dry_run: bool = True) -> Dict[str, Any]:
    """Merge duplicate :Disease pairs onto their canonical node.

    Defaults to dry_run=True: this moves edges and deletes nodes. Idempotent —
    a second run finds no duplicate groups left. Returns a report.
    """
    with driver.session(database=database) as session:
        rows = [dict(r) for r in session.run(_SURVEY)]

    plan = _plan_from_rows(rows)

    if dry_run:
        logger.info(
            "DRY RUN: would merge %d duplicate disease group(s): %s",
            len(plan),
            ", ".join(
                f"{p['canonical_norm']} <- {[d['norm'] for d in p['duplicates']]}"
                for p in plan
            ) or "none",
        )
        return {"dry_run": True, "planned": plan}

    merged = []
    with driver.session(database=database) as session:
        for p in plan:
            dup_eids = [d["eid"] for d in p["duplicates"]]
            # Preserve every folded surface name AND normalized value as aliases
            # so a lookup by either form can still resolve to the survivor.
            aliases = sorted({
                v for d in p["duplicates"] for v in (d["name"], d["norm"]) if v
            })
            merged_norms = [d["norm"] for d in p["duplicates"]]
            survivor = session.run(
                _MERGE,
                canon_eid=p["canonical_eid"],
                dup_eids=dup_eids,
                aliases=aliases,
                merged_norms=merged_norms,
            ).single()["survivor"]
            merged.append({
                "canonical_norm": p["canonical_norm"],
                "folded": merged_norms,
                "aliases": aliases,
                "survivor_eid": survivor,
            })
            logger.info(
                "Merged %s <- %s (aliases: %s)",
                p["canonical_norm"], merged_norms, aliases,
            )

    logger.info("Disease merge complete: %d group(s) merged", len(merged))
    return {"dry_run": False, "merged": merged}
