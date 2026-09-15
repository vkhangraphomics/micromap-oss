# Provenance

This document covers what MapForge records about each submission, how to
audit a contribution after the fact, how to roll one back, and how to opt out
of the provenance writer entirely for stand-alone curated KGs.

**Audience:** an operator auditing recent contributions, an investigator
tracing the provenance of a node, or an owner of a stand-alone curated KG
who needs to decide whether the opt-out applies.

## Two layers of provenance

MapForge maintains two complementary layers — they answer different
questions:

| Layer | Question | Where it lives |
|---|---|---|
| **Ingestion-time provenance** | "Who put this data here, when, from what source?" | `:Contribution` nodes in the destination Neo4j |
| **Federation-time provenance** | "Which deployments answered this query?" | `provenance.sources` field on the federation gateway's query responses |

This document is about the first layer. The federation layer is documented in
[`new-instance-executor-contract.md#provenance--two-layers`](new-instance-executor-contract.md#provenance--two-layers).

## What the writer records

On every successful `mapforge submit` (when `provenance.enabled` is `true`,
the default), the writer adds the following to the destination Neo4j:

```cypher
(:Organization {id: <contributor-org>})
    -[:CONTRIBUTED]->
(:Contribution {
    mapping_sha256: <hex>,
    source_sha256:  <hex>,
    destination:    "micromap-core" | "registry-only" | "new-federated-instance",
    submitted_at:   <ISO-8601 UTC>,
    resolved_count: <int>,
    unresolved_count: <int>,
    ambiguous_count: <int>,
    organization_id: <multi-tenant tag>
})
    -[:APPROVED_BY]->
(:Reviewer {name: <reviewer-name>})
```

**Eight core fields (E3 adds source attribution properties — see below), three nodes, two edges:**

- `:Organization` is the *contributor* org. Distinct from the multi-tenant
  `organization_id` property on data nodes (which is the *target* tenant tag).
- `:Reviewer` is the name passed to `mapforge approve --reviewer`. One node
  per unique name (MERGE on `name`).
- `:Contribution` carries the per-submission facts: source hash, mapping
  hash, destination, timestamp, and the resolved/unresolved/ambiguous counts
  from `resolution.json`.
- `(:Organization)-[:CONTRIBUTED]->(:Contribution)` and
  `(:Contribution)-[:APPROVED_BY]->(:Reviewer)` close the loop.

### Source attribution properties (E3, optional)

When the bundle's `mapping.yaml::source` declares any of the E3 source-
attribution fields (`license`, `url`, `doi`, `contact`, `version`,
`accessed_at`, `ethics_ref`, plus the always-present `name`), they
appear on the `:Contribution` node as `source_<field>` properties:

- `c.source_name`
- `c.source_license`
- `c.source_url`
- `c.source_doi`
- `c.source_contact`
- `c.source_version`
- `c.source_accessed_at`
- `c.source_ethics_ref`

All optional. Pre-E3 bundles produce `:Contribution` nodes without
these properties; queries filter via `WHERE c.source_license IS NOT NULL`
to scope to E3-attributed contributions.

Re-submission semantics: the MERGE key is unchanged
(`{mapping_sha256, source_sha256, organization_id}`), so a second
submission with edited e3 metadata MERGEs to the same `:Contribution`
node and the SET clause overwrites — latest wins. Audit history of
metadata changes is not captured.

### Governance properties (E4, sparse)

When `mapforge submit --force` is used to bypass the manifest-approval
gate on an unapproved bundle, the resulting `:Contribution` node carries
`force_submitted: true`. Otherwise — approved-then-submitted, or
`--force` flag passed redundantly on an already-approved bundle — the
field is absent.

Audit query — list all bypass events:

```cypher
MATCH (c:Contribution)
WHERE c.force_submitted = true
RETURN c
```

Pre-E4 `:Contribution` nodes don't have this property at all, so the
`WHERE c.force_submitted = true` filter scopes cleanly to post-E4
bypasses. To find Contributions that explicitly were NOT force-bypassed,
filter `WHERE c.force_submitted IS NULL` — this picks up both legacy
(pre-E4) nodes and post-E4 properly-approved Contributions.

Re-submission semantics: the MERGE key is unchanged, so a contributor
who force-submits, then later re-approves and resubmits the same
bundle, retains the `force_submitted: true` stamp from the first
submission. The audit trail records the worst-case state for the
Contribution node, which is the conservative choice for governance.

## Where the writer writes

The Contribution writer uses the **same Neo4j driver** that submit just used
for the data write. This means:

- For `destination: micromap-core`, the Contribution lands in the same graph
  as the data.
- For `destination: new-federated-instance`, the Contribution lands in the
  **remote** instance's Neo4j (where the data is), not at the central hub.
  The central hub's federation registry holds the `:FederatedSource` record;
  the per-contribution Contribution node lives with the data.
- For `destination: registry-only`, no data is written and no Contribution is
  written either (there's no submit-side bolt write to follow).

This is intentional: the Contribution node is co-located with the data it
describes. If you can read a federated `:Disease` node, you can also see its
provenance.

## Auditing a contribution

### Find every contribution from a specific organization

```cypher
MATCH (org:Organization {id: 'acme-pharma'})-[:CONTRIBUTED]->(c:Contribution)
RETURN c.submitted_at, c.destination, c.resolved_count, c.unresolved_count
ORDER BY c.submitted_at DESC
LIMIT 50;
```

### Find every contribution approved by a specific reviewer

```cypher
MATCH (c:Contribution)-[:APPROVED_BY]->(rev:Reviewer {name: 'alice'})
RETURN c.submitted_at, c.organization_id, c.destination
ORDER BY c.submitted_at DESC;
```

### Find contributions with a known source-file hash

Useful when you have a source file in hand and want to know if it was already
contributed:

```cypher
MATCH (c:Contribution {source_sha256: '<hex>'})
RETURN c, [(org)-[:CONTRIBUTED]->(c) | org.id] AS contributing_orgs;
```

### List recent contributions

```cypher
MATCH (c:Contribution)
RETURN c.submitted_at, c.organization_id, c.destination,
       c.resolved_count, c.unresolved_count, c.ambiguous_count
ORDER BY c.submitted_at DESC
LIMIT 100;
```

## Rollback

There's no `mapforge rollback` command. Rolling back a submission means
removing the data nodes and relationships it MERGEd, plus the
`:Contribution` node itself. The right approach depends on how much you
want to remove.

### Recipe 1: remove every node tagged with a specific contribution's `organization_id`

Use this when the contribution was the *only* source of nodes for that
tenant tag:

```cypher
MATCH (c:Contribution {mapping_sha256: '<hex>', source_sha256: '<hex>'})
WITH c, c.organization_id AS org_id
MATCH (n {organization_id: org_id})
DETACH DELETE n;
```

**Danger:** this removes every node with that `organization_id`, including
ones written by other contributions from the same tenant. Use only when you
know this contribution was the tenant's only writer.

### Recipe 2: remove just the Contribution node

Use this when the data should stay but the provenance record was wrong
(e.g., reviewer name was a placeholder, you want to re-record it):

```cypher
MATCH (c:Contribution {mapping_sha256: '<hex>', source_sha256: '<hex>'})
DETACH DELETE c;
```

The data nodes the contribution MERGEd remain. Future `mapforge submit` calls
that touch the same nodes will accumulate properties via `SET n += row.props`
— no duplicates.

### Recipe 3: targeted rollback by re-running submit with negation

For sources where you know the exact set of `MERGE` keys, you can write a
custom Cypher that DETACH DELETEs those specific nodes. There's no MapForge
sugar for this — you compose it from `bundle/cypher/*.params.json`:

```python
import json
with open("bundle/cypher/nodes_Taxon.params.json") as f:
    params = json.load(f)

keys = [row["merge_value"] for row in params["batch_ncbi_tax_id__contributor"]]
org_id = params["organization_id"]

# In cypher-shell or Python:
# UNWIND $keys AS k
# MATCH (n:Taxon {ncbi_tax_id: k, organization_id: $org_id})
# DETACH DELETE n;
```

This is the safest rollback for production — it touches only the exact nodes
the contribution wrote.

## Opt-out: stand-alone curated KGs

Set `provenance.enabled: false` in your routing-policy.yaml:

```yaml
provenance:
  enabled: false

rules: []
default:
  destination: micromap-core
```

When `false`:

- The writer is **not** invoked. No `:Organization`, `:Reviewer`, or
  `:Contribution` nodes are created.
- `--reviewer` is no longer required at submit time. If passed, it's silently
  ignored with a stderr note (`--reviewer ignored: provenance writer disabled
  by routing-policy`).
- `mapforge approve --reviewer <name>` still records the reviewer into the
  bundle's `manifest.json` for audit, but that field becomes dead data
  downstream.

For the full motivation — when to opt out, the destination-naming caveat for
stand-alone KGs, the forward-compat note — see
[`new-instance-executor-contract.md#stand-alone-curated-kgs--provenance-opt-out`](new-instance-executor-contract.md#stand-alone-curated-kgs--provenance-opt-out).

## Multi-tenancy and Contribution nodes

The `:Contribution` node carries an `organization_id` property identical to
the one stamped on the data it describes. This means:

- Two contributions from different tenants that happen to share a
  `(mapping_sha256, source_sha256)` pair produce *distinct* Contribution
  nodes (MERGE key includes `organization_id`).
- A query restricted to `WHERE c.organization_id = $tenant` shows only that
  tenant's contributions — useful for tenant-scoped audit dashboards.

## Confidentiality

The Contribution node stores the **hash** of the source file, not the source
itself. Even if your destination Neo4j is queried by a different tenant,
they can't reconstruct the source contents from the Contribution. They can,
however, see who contributed it and when. If that's sensitive, consider
opting out (the data still lands, but no audit-trail node is created in the
graph).

## Forward-compat note

If a bundle is authored with `provenance: { enabled: false }` but submitted
with an *older* MapForge binary that predates the flag (anything before
[#113](https://github.com/vkhangraphomics/MicroMap/pull/113)), the old binary
will silently run the writer — bundles are not version-pinned. Keep MapForge
installs in sync across your fleet to avoid this.

## Cross-references

- The flag itself: [`routing-policy.md#provenance-block`](routing-policy.md#provenance-block)
- Contract for the federated path: [`new-instance-executor-contract.md`](new-instance-executor-contract.md)
- Source code: [`micromap_mapforge/provenance/contribution.py`](../micromap_mapforge/provenance/contribution.py)
- Bundle layout: [`bundle-anatomy.md`](bundle-anatomy.md)
