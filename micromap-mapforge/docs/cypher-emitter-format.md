# Cypher Emitter Format

This document describes exactly what `mapforge emit` writes to `bundle/cypher/`,
so a reviewer can read it before submit touches Neo4j and an ops engineer can
reason about idempotency, performance, and rollback.

**Audience:** Cypher-literate readers who want to understand what will land
in their graph.

## File pairs

For every entity label and every relationship type in the mapping, emit writes
two files:

```
cypher/
├── nodes_<Label>.cypher              ← static Cypher (UNWIND + MERGE)
├── nodes_<Label>.params.json         ← per-bundle row data
├── rels_<TYPE>.cypher
└── rels_<TYPE>.params.json
```

The split is deliberate. Cypher files are deterministic given the mapping —
two runs of `emit` against the same mapping produce byte-identical `.cypher`
files. Params files carry the actual row data and change per bundle. This
makes engine-change diffs (Cypher) cleanly separable from data-change
diffs (params) during review.

## Node format

`nodes_Taxon.cypher`:

```cypher
// Generated Cypher — Taxon nodes (parameterized; see nodes_Taxon.params.json)

UNWIND $batch_ncbi_tax_id__contributor AS row
MERGE (n:Taxon {ncbi_tax_id: row.merge_value, organization_id: $organization_id})
SET n += row.props;
```

`nodes_Taxon.params.json`:

```json
{
  "organization_id": "disbiome-demo",
  "batch_ncbi_tax_id__contributor": [
    {
      "merge_value": "853",
      "props": {
        "scientific_name": "Faecalibacterium prausnitzii",
        "rank": "species",
        "source_origin": "contributor"
      }
    },
    {
      "merge_value": "239935",
      "props": {
        "scientific_name": "Akkermansia muciniphila",
        "rank": "species",
        "source_origin": "contributor"
      }
    }
  ]
}
```

**Pattern reading:**

- `UNWIND $batch_<match_field>__<batch_tag> AS row` — Cypher iterates over the
  rows in one round-trip. The batch parameter name encodes the match-on field
  and a contextual tag (`contributor` here, indicating the rows came from a
  contributor bundle vs. inferred).
- `MERGE (n:Taxon {ncbi_tax_id: row.merge_value, organization_id: $organization_id})`
   — MERGE key is the **match field** plus the **organization_id**. Two
  contributions from different orgs that happen to share an `ncbi_tax_id`
  produce distinct nodes — multi-tenancy is enforced at the key level.
- `SET n += row.props` — non-key properties are merged in. Re-running the
  same submit overwrites property values but never duplicates the node.

**Idempotency:** because MERGE matches on the natural key + org, re-running
the same bundle yields exactly the same node count. No duplicates.

**`source_origin`** is automatically added — for contributor bundles it's
`"contributor"`; for engine-inferred entities (a future code path) it would be
something else. Provenance-by-property.

## Relationship format

`rels_MENTIONED_IN.cypher`:

```cypher
// Generated Cypher — MENTIONED_IN relationships (parameterized; see rels_MENTIONED_IN.params.json)

UNWIND $batch_ncbi_tax_id__pmid AS row
MATCH (a:Taxon {ncbi_tax_id: row.from}),
      (b:Paper {pmid: row.to})
MERGE (a)-[r:MENTIONED_IN {organization_id: $organization_id}]->(b)
SET r += row.props;
```

`rels_MENTIONED_IN.params.json`:

```json
{
  "organization_id": "disbiome-demo",
  "batch_ncbi_tax_id__pmid": [
    {
      "from": "853",
      "to": "18936492",
      "props": {
        "context": "microbiome_association"
      }
    },
    {
      "from": "853",
      "to": "23042570",
      "props": {
        "context": "microbiome_association"
      }
    }
  ]
}
```

**Pattern reading:**

- `UNWIND $batch_<from_field>__<to_field> AS row` — relationship rows are
  named after their from/to anchor fields.
- `MATCH (a:Taxon {ncbi_tax_id: row.from}), (b:Paper {pmid: row.to})` — the
  endpoints are looked up by the same match fields the node MERGEs used.
  Note: **MATCH**, not MERGE. The nodes must already exist (the node Cypher
  ran first). This is the [#90 fix](https://github.com/vkhangraphomics/MicroMap/issues/90):
  using MERGE here with the org-scoped key would resurrect duplicates of any
  curated node that happens to have the same natural key, which violates the
  "relationships are additive over curated nodes" contract.
- `MERGE (a)-[r:MENTIONED_IN {organization_id: $organization_id}]->(b)` —
  the relationship itself is org-scoped at the MERGE level. Different orgs'
  relationships are distinct edges even between the same node pair.
- `SET r += row.props` — relationship properties accumulate the same way as
  node properties.

**Multi-org behavior on the same edge:** `(a)-[:MENTIONED_IN]->(b)` is one
edge per `(a, b, organization_id)` triple. Two orgs claiming the same
Taxon-Paper mention create two parallel edges.

## Run order

`mapforge submit` runs the files in this order:

1. All `nodes_<Label>.cypher` files (one transaction each).
2. All `rels_<TYPE>.cypher` files (one transaction each).
3. Then `_write_contribution_for_bundle` (if `provenance.enabled` is true).

Within each transaction, the UNWIND batches the entire `params.json` into a
single round-trip — one network call to Neo4j per file.

## What gets `MERGE`d, what gets `MATCH`ed

| Operation | Cypher op | Key |
|---|---|---|
| Entity nodes (e.g., `:Taxon`) | `MERGE` | `(match_field, organization_id)` |
| Relationship endpoints | `MATCH` | `(match_field)` only, no org filter |
| Relationships themselves | `MERGE` | `(from, to, type, organization_id)` |
| `:Contribution` provenance | `MERGE` | `(mapping_sha256, source_sha256, organization_id)` |
| `:Organization` | `MERGE` | `(id)` (org's own ID, not the multi-tenant tag) |
| `:Reviewer` | `MERGE` | `(name)` |

**The "MATCH for relationship endpoints" rule** is the one subtle case. A
contributor bundle resolves its source terms to entities that may have been
curated by MicroMap upstream (e.g., a Taxon node MicroMap loaded from NCBI).
Relationship endpoints MATCH the resolved entity by natural key without an
`organization_id` filter, so a partner's `ASSOCIATED_WITH_DISEASE` edge can
point at MicroMap's curated `:Disease` node. See issue #90 for the
historical context.

## Parameter binding

All values flow through `$param` bindings. **Nothing is interpolated into the
Cypher string.** A semicolon in a reviewer name or organization id cannot
fragment the statement. The relevant security guarantee is:

> Cypher strings in `bundle/cypher/*.cypher` are deterministic and contain no
> values from the bundle's content. Re-running emit on a different bundle
> produces byte-identical `.cypher` files if and only if the mapping is
> byte-identical.

## Inspecting a single statement before submit

You can dry-run any file pair against your Neo4j without submit:

```bash
cypher-shell -u neo4j -p test \
    -P "params=$(cat bundle/cypher/nodes_Taxon.params.json)" \
    < bundle/cypher/nodes_Taxon.cypher
```

(For `cypher-shell` < 5, see its docs for the params-file syntax; `cypher-shell`
≥ 5 supports `-P` with a JSON literal.)

Or in Python:

```python
from neo4j import GraphDatabase
import json

driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "test"))
with open("bundle/cypher/nodes_Taxon.cypher") as f:
    cypher = f.read()
with open("bundle/cypher/nodes_Taxon.params.json") as f:
    params = json.load(f)
with driver.session() as session:
    summary = session.run(cypher, **params).consume()
    print(summary.counters)
```

## Why this format and not something simpler

A flatter alternative — one `INSERT … VALUES`-style statement per row —
would be easier to read line-by-line but produces N round-trips for N rows.
At MicroMap scale (tens of thousands of rows per bundle) the UNWIND-batched
pattern is two orders of magnitude faster and the params split keeps Cypher
diffs cleanly separable from data diffs during review.

## Limits

- One UNWIND batch = one transaction. For very large bundles (>100K rows
  per label) Neo4j may exceed its transaction memory budget; the engine
  doesn't currently chunk further. This is tracked under issue #79's MAC-41
  carryover items.
- Property-name collisions across labels are not detected at emit time. If
  your `Taxon` mapping defines a `name` column and so does `Disease`, both
  appear in their respective nodes — that's the desired behavior, but be
  aware that a single Cypher query joining them needs to disambiguate.

## Cross-references

- Bundle layout: [`bundle-anatomy.md`](bundle-anatomy.md)
- Provenance writer specifics: [`provenance.md`](provenance.md)
- Federation routing (writes go to a remote): [`new-instance-executor-contract.md`](new-instance-executor-contract.md)
