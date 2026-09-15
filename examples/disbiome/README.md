# Curated Disbiome example bundle

End-to-end walkthrough of the MapForge ingestion pipeline against a curated
50-row sample of well-known microbiome–disease associations. Each row is a
real published association (with PMID + DOI) and matches Disbiome's CSV
column shape, so the same pipeline that ingests the full Disbiome dump
works on this sample.

Use this bundle to:
- learn the MapForge `inspect → map → resolve → plan → emit → submit → approve` flow,
- validate engine behavior on real-shaped data on a fresh machine in well
  under five minutes,
- demonstrate resolver behavior against MicroMap's existing NCBI Taxonomy
  and Disease Ontology nodes.

> **What's in the data.** The 50 rows cover 32 distinct organisms (species
> + a couple of genus-level rows) and 24 distinct diseases. Each row carries
> direction (`increased`/`decreased`), sample type (stool / colonic biopsy
> / oral swab / etc.), detection method, PMID, and DOI. Together that's
> enough to exercise the resolver registry (two batched name lookups +
> one PMID lookup per row), the Cypher emitter's three node buckets
> (Taxon, Disease, Paper) plus two relationship types
> (`ASSOCIATED_WITH_DISEASE`, `MENTIONED_IN`), and the routing policy
> (partner-tier + public sensitivity → `micromap-core`).

---

## Prerequisites

- Python 3.11+
- A running MicroMap Neo4j with the NCBI Taxonomy and Disease Ontology
  nodes already loaded. Easiest path is the project's docker compose
  stack from the repo root (see [the project README](../../README.md)
  for `.env` setup):

  ```bash
  docker compose up -d
  docker compose exec micromap-api python -m database.load_knowledge_graph --taxonomy --disbiome
  ```

  This populates the existing-entity tables the MapForge resolver
  matches against. Without these nodes loaded, every row falls through
  to "contributor-novel" (which is a legitimate flow but isn't what
  this example demonstrates).
- MapForge installed in editable mode:

  ```bash
  pip install -e micromap-mapforge[dev]
  ```

## Walkthrough

All commands run from the **repo root**. The bundle's outputs land in
`examples/disbiome/bundle-out/` and that directory is `.gitignore`d.

### 1. Inspect

```bash
mapforge inspect examples/disbiome/disbiome_sample.csv \
    --out examples/disbiome/bundle-out
```

Writes an inspection report covering each column's inferred type,
null-rate, distinct count, and three sample values. Open
`examples/disbiome/bundle-out/inspection-report.md` to verify the
inspector identified `ncbi_taxid` as a numeric id, `doid` as another
id, `microorganism`/`disease` as free-text terms, and `pmid` as a
literature reference.

### 2. Map

Copy the hand-authored `mapping.yaml` into the bundle output (`map`
would otherwise overwrite it with a fresh heuristic draft):

```bash
cp examples/disbiome/mapping.yaml examples/disbiome/bundle-out/mapping.yaml
```

The mapping declares three entities (`Taxon`, `Disease`, `Paper`)
and two relationships (`ASSOCIATED_WITH_DISEASE`,
`MENTIONED_IN`). It matches Disbiome's column names exactly so a
larger Disbiome export drops in without edits.

If you'd rather see what the heuristic mapper drafts, run:

```bash
mapforge map examples/disbiome/disbiome_sample.csv \
    --out examples/disbiome/bundle-out --mode heuristic
```

The hand-authored mapping is more complete (it adds the relationship
blocks the heuristic mode leaves empty).

### 3. Resolve

```bash
mapforge resolve --bundle examples/disbiome/bundle-out \
    --neo4j-uri    bolt://localhost:7687 \
    --neo4j-user   neo4j \
    --neo4j-password password \
    --neo4j-database graphomics
```

The resolver registry preloads existing Taxon (by `ncbi_tax_id` + name)
and Disease (by `doid` + `name_normalized`) entries from Neo4j, then
matches every CSV row against them. On a populated dev KG, every Taxon
row with a real `ncbi_taxid` resolves (the resolver hits the preload
cache rather than per-row queries — the reason `resolve` is fast even
on much larger bundles). Disease resolution uses
`normalize_disease_name`, so `"Crohn's disease"` matches the canonical
form regardless of the exact case/punctuation the source used.

Writes:
- `bundle-out/resolution.json` — per-row resolution decisions
- `bundle-out/unresolved.md` — human-readable list of any unresolved
  terms (expected: small or empty against a populated KG; large
  against an empty one).

### 4. Plan

```bash
mapforge plan --bundle examples/disbiome/bundle-out \
    --organization-id disbiome-example \
    --policy   examples/disbiome/routing-policy.yaml \
    --contributor examples/disbiome/contributor.yaml
```

`contributor.yaml` declares the bundle as `partner`-tier, `public`
sensitivity. The routing policy's second rule matches that and routes
to `micromap-core` (additive to the core graph). Writes
`bundle-out/routing.yaml`.

### 5. Emit

```bash
mapforge emit --bundle examples/disbiome/bundle-out
```

Generates per-label Cypher under `bundle-out/cypher/`:

- `nodes_Taxon.cypher`, `nodes_Disease.cypher`, `nodes_Paper.cypher`
- `rels_ASSOCIATED_WITH_DISEASE.cypher`, `rels_MENTIONED_IN.cypher`
- `manifest.json` — bundle hash, file checksums, approval state
- `INGEST_REPORT.md` — pre-merge summary for the reviewer

Resolved entities emit as `MATCH (n:Label {field: row.merge_value})`
(no org-scope — the shared existing node is what we link); any
fall-through entities emit as `MERGE (n:Label {field: row.merge_value,
organization_id: $organization_id})` with the contributor's org tag
(see #90 for the reasoning).

### 6. Approve + submit

```bash
mapforge approve --bundle examples/disbiome/bundle-out \
    --reviewer your-name

mapforge submit --bundle examples/disbiome/bundle-out \
    --reviewer your-name \
    --neo4j-uri    bolt://localhost:7687 \
    --neo4j-user   neo4j \
    --neo4j-password password \
    --neo4j-database graphomics
```

`approve` flips the `manifest.json.approved` flag and records the
reviewer. `submit` executes the Cypher batches in a single transaction
per file, then writes a `Contribution` node back to MicroMap recording
the source SHA-256, mapping SHA-256, destination, row counts, and
reviewer for full provenance.

---

## Verifying the result

Open the Neo4j browser at <http://localhost:7474> and run:

```cypher
// Newly-added (or now-linked) Taxon→Disease edges from this bundle:
MATCH (t:Taxon)-[r:ASSOCIATED_WITH_DISEASE]->(d:Disease)
WHERE r.evidence_level = 'literature_curated'
  AND r.organization_id = 'disbiome-example'
RETURN t.name, d.name, r.direction, r.pmid
ORDER BY t.name
LIMIT 25;
```

And check the contribution record:

```cypher
MATCH (c:Contribution {contributor: 'disbiome-curated-example'})
RETURN c.organization_id, c.reviewer, c.submitted_at,
       c.resolved_count, c.unresolved_count
ORDER BY c.submitted_at DESC
LIMIT 1;
```

Expected on a fully-populated dev KG (NCBI Taxonomy + Disbiome already
loaded): `resolved_count ≈ 50`, `unresolved_count` close to 0.

---

## Re-running cleanly

The bundle output directory is throwaway; clear it and start over:

```bash
rm -rf examples/disbiome/bundle-out
```

To clean up the example's contribution from Neo4j:

```cypher
MATCH (n)-[r {organization_id: 'disbiome-example'}]->()
DELETE r;
MATCH (n {organization_id: 'disbiome-example'})
WHERE n.source_origin = 'contributor'
DETACH DELETE n;
MATCH (c:Contribution {organization_id: 'disbiome-example'})
DETACH DELETE c;
```

(Resolved Taxon and Disease nodes aren't deleted — they predate this
bundle and remain shared.)

---

## Data attribution

Each row's `pmid` and `doi` point at the peer-reviewed publication
that reported the association. The set is hand-curated — it's not a
slice of any particular Disbiome export — but it follows Disbiome's
column shape so a real Disbiome dump (or a partner's CSV in that
same shape) can use the same `mapping.yaml` without edits.
