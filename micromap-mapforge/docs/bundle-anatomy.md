# Bundle Anatomy

A *bundle* is a directory MapForge writes to, one stage at a time, until it's
ready to submit. This document covers every file MapForge writes, with field
references and worked examples.

**Audience:** anyone who needs to read what MapForge produced — code reviewers,
ops engineers debugging a stalled submission, auditors verifying a
contribution.

## Bundle layout (post-stage-6)

```
bundle/
├── <source file>                  ← original input, copied or referenced
├── inspection-report.md           ← stage 1: human-readable column profile
├── inspection.json                ← stage 1: machine-readable profile
├── mapping.yaml                   ← stage 2: column → ontology mapping
├── resolution.json                ← stage 3: per-entity resolver verdicts
├── unresolved.md                  ← stage 3: humans look here first
├── routing.yaml                   ← stage 4: destination + provenance
├── cypher/
│   ├── nodes_<Label>.cypher       ← stage 5: parameterized MERGE (one per label)
│   ├── nodes_<Label>.params.json
│   ├── rels_<TYPE>.cypher         ← stage 5: parameterized MERGE (one per rel type)
│   └── rels_<TYPE>.params.json
├── INGEST_REPORT.md               ← stage 5: reviewer's main read
└── manifest.json                  ← stage 5/6: hashes + approval state
```

Files added by the contributor (`contributor.yaml`, `routing-policy.yaml`) may
also live in the bundle directory — MapForge tolerates them but doesn't write
them.

The bundle also contains a `schema_config.yaml` written by `mapforge map`:

> The bundle's `schema_config.yaml` is sourced from either a built-in
> template (when `mapforge map --schema-config <name>` is used) or a
> user-supplied path. The bundle's manifest hashes the file so the schema
> is sealed into the bundle's identity regardless of source. See
> [`docs/schemas/discipline-templates.md`](./schemas/discipline-templates.md)
> for the built-ins.

---

## `mapping.yaml`

The single most important artifact. Defines what the source's columns mean in
ontology terms. Schema lives at
[`micromap_mapforge/mapping/mapping.schema.json`](../micromap_mapforge/mapping/mapping.schema.json);
the full field-by-field reference is at
[`schemas/mapping-yaml.md`](schemas/mapping-yaml.md).

```yaml
source:
  name: disbiome_curated_example      # human-readable identifier
  format: csv                          # csv | tsv | json | jsonl | parquet | sql_dump | schema_adapter
  path: disbiome_sample.csv            # absolute, or relative to the bundle dir
  description: >                       # optional, free-form
    Curated microbiome-disease associations from primary literature.

entities:
  - label: Taxon                       # strict-mode label enum (see below)
    match_on: ncbi_tax_id              # property the resolver uses for lookup
    columns:                           # ontology field → source column
      ncbi_tax_id: ncbi_taxid
      scientific_name: microorganism
      rank: taxonomic_rank
    confidence: EXTRACTED              # EXTRACTED | INFERRED | AMBIGUOUS

  - label: Disease
    match_on: name_normalized          # the resolver applies `normalizer` first
    normalizer: normalize_disease_name
    columns:
      name: disease
      doid: doid
    confidence: EXTRACTED

  - label: Paper
    match_on: pmid
    columns:
      pmid: pmid
      doi: doi
    confidence: EXTRACTED

relationships:
  - type: ASSOCIATED_WITH_DISEASE
    from: "Taxon(ncbi_tax_id=row.ncbi_taxid)"
    to:   "Disease(name_normalized=normalize(row.disease))"
    properties:
      direction: qualitative_outcome
      sample_type: sample
      method: method
      pmid: pmid
      doi: doi
      evidence_level: { constant: literature_curated }
    confidence: EXTRACTED
```

**`source.sha256`** (sealed at `mapforge map` time, post-E5) — the SHA-256
of the source file's bytes as they were when `mapforge map` ran. This is
read by `mapforge submit` to populate `:Contribution.source_sha256` without
re-opening the source file. The seal makes bundles portable across
consumers — Workbench-staged paths, local dev disk, MCP session
workspaces — because submit no longer needs the source file to be present
at its original path. Bundles built before E5 lack this field; submit
falls back to recomputing from the on-disk file when present, and errors
loudly otherwise (refusing to write an empty `source_sha256` that would
corrupt the audit trail via MERGE collisions).

**Strict mode vs permissive mode:** if `source.format` is `schema_adapter`, the
entity-mapping shape is permissive — any label is allowed. For every other
format, the *strict* mode is in force and `label` must be one of: `Taxon`,
`Disease`, `Metabolite`, `Drug`, `Gene`, `Protein`, `Pathway`, `Paper`,
`BodySite`. (Relaxing this is tracked under issue #74.)

**Relationship from/to syntax:** each is a string of the form
`<Label>(<match_field>=<expression>)`. The expression can reference any
column via `row.<column>`, and may apply an in-process normalizer
(`normalize(row.<column>)`). The emitter parses these into MATCH clauses.

**Property values in relationships:** each property is either a column name
(string — value taken from that row's column) or a constant
(`{constant: <value>}`).

**Confidence enum:** every entity and relationship carries a `confidence`
tag that propagates to the destination graph. Used downstream to filter
auto-inferred vs human-curated triples.

---

## `inspection-report.md` and `inspection.json`

Stage 1's output. The markdown report is the human-readable form; the JSON
file carries the same content for downstream tools.

```markdown
# Inspection Report

- **Source:** `examples/disbiome/disbiome_sample.csv`
- **Format:** `csv`
- **Row count (estimate):** 50
- **Columns:** 11

## Columns

| Name | Inferred Type | Null Rate | Distinct | Samples |
|---|---|---|---|---|
| `microorganism` | string | 0.00% | 37 | 'Faecalibacterium prausnitzii', … |
| `ncbi_taxid`    | integer | 0.00% | 37 | 853, 853, 853 |
| `disease`       | string | 0.00% | 22 | "Crohn's disease", "ulcerative colitis", … |
```

**Type inference rules:**
- `integer` if all non-null values match `^-?\d+$`.
- `float` if all match a numeric pattern with `.`.
- `string` otherwise.
- `null` row-rate is computed against the total sampled rows.

The mapper consumes `inspection.json` when running in `--mode llm` to give
the LLM column-shape context.

---

## `resolution.json` and `unresolved.md`

Stage 3's outputs. `resolution.json` is the machine-readable verdict for every
entity instance:

```json
{
  "resolved": [
    {
      "entity_label": "Taxon",
      "source_term": "562",
      "candidates": [
        {
          "node_id": "ncbi_tax_id:562",
          "match_type": "EXTRACTED",
          "score": 1.0,
          "reason": "exact identifier match on ncbi_tax_id",
          "merge_field": "ncbi_tax_id",
          "merge_value": "562"
        }
      ]
    }
  ],
  "unresolved": [
    { "entity_label": "Taxon", "source_term": "853", "candidates": [] }
  ],
  "ambiguous": [],
  "resolved_count": 1,
  "unresolved_count": 1,
  "ambiguous_count": 0
}
```

**Verdict categories:**
- **resolved** — exactly one candidate above threshold.
- **unresolved** — zero candidates.
- **ambiguous** — multiple candidates with no clear winner; `candidates` is non-empty.

`unresolved.md` is the human-friendly summary:

```markdown
# Unresolved & Ambiguous Entities

- **Resolved:** 0
- **Unresolved:** 106
- **Ambiguous:** 0

## Unresolved — Disease

- `Crohn's disease` — no candidates found
- `ulcerative colitis` — no candidates found
- …
```

When unresolved counts are surprisingly high, the reviewer reads this file
first to decide whether the mapping is wrong, the target graph is missing
reference data, or both.

---

## `routing.yaml`

Stage 4's output. Captures destination + organization + provenance metadata.

```yaml
destination: micromap-core           # or registry-only | new-federated-instance
organization_id: disbiome-demo       # multi-tenant tag stamped on every node + rel
provenance:
  contributor: disbiome-curated-example
  submitted_at: '2026-06-04T23:42:03.112972+00:00'
  mapping_version: sha256:b94a69af9bcedb7f9b8a70879b9f1a0821c9eccad90b2925b543d47efce3d266
  enabled: true                      # optional; defaults to true; see provenance.md
```

For `destination: new-federated-instance`, an additional `destinations` block
carries the federation config:

```yaml
destinations:
  new-federated-instance:
    federation:
      source_id: acme-pharma
      display_name: "Acme Pharma"
      base_url: https://micromap.acme.example.com
      bolt_uri: bolt+s://acme-neo4j.acme.example.com:7687
      bolt_auth_ref: env:ACME_BOLT_PASSWORD
      auth_ref: env:ACME_FEDERATION_TOKEN
      capabilities: [diseases.taxa, taxa.diseases]
      organization_id: acme
```

Full schema is at
[`micromap_mapforge/emit/routing_policy.schema.json`](../micromap_mapforge/emit/routing_policy.schema.json).
The federation-instance contract is documented in
[`new-instance-executor-contract.md`](new-instance-executor-contract.md).

---

## `cypher/` — generated Cypher

One pair of files per entity label and per relationship type:

- `nodes_<Label>.cypher` + `nodes_<Label>.params.json`
- `rels_<TYPE>.cypher` + `rels_<TYPE>.params.json`

Full format reference is at [`cypher-emitter-format.md`](cypher-emitter-format.md).

**Why split params from cypher?** Cypher files are static — the same MERGE
statement runs against any input. Params files are per-bundle — they carry the
actual row data. Diffing two bundles' Cypher files tells you if the engine
changed; diffing the params files tells you if the data changed.

---

## `INGEST_REPORT.md`

Stage 5's reviewer-facing summary. The reviewer reads this *before* approve.

```markdown
# Ingest Report

## Resolution Summary
- **Resolved:** 0
- **Unresolved:** 106
- **Ambiguous:** 0

## Confidence Breakdown (resolved entities)
- **EXTRACTED:** 0
- **INFERRED:** 0

See `unresolved.md` for unresolved / ambiguous items.
```

The report intentionally surfaces both counts and confidence breakdown — a
reviewer can spot at-a-glance whether a submission is auto-inferred (potentially
noisy) or hand-curated.

---

## `manifest.json`

Stage 5's tamper-evident hashing record, optionally updated by stage 6 with
the reviewer's approval.

```json
{
  "approved": true,
  "approved_at": "2026-06-04T23:45:56.050963+00:00",
  "reviewer": "alice",
  "files": {
    "INGEST_REPORT.md": {
      "sha256": "bd3006f156c762f0faf5e91e3014c80eac218c0615942344abe6780c1d4e5644",
      "size": 251
    },
    "cypher/nodes_Taxon.cypher": {
      "sha256": "3b5f55d0fc24a77b7c90ea25e5fd82ee5a426f6646c88ee91b0327bb1e0e3c3d",
      "size": 192
    },
    "cypher/nodes_Taxon.params.json": {
      "sha256": "…",
      "size": 4521
    },
    "...": "..."
  }
}
```

**Hash inventory:** every regular file in the bundle (except `manifest.json`
itself) gets a sha256 + size entry. If any bundle file changes after stage 6,
submit refuses to run unless you pass `--force`.

**Approval fields:** `approved` is `true` after stage 6, `false` or absent
before. `reviewer` is the `--reviewer` value passed to `approve`. `approved_at`
is the wall-clock UTC timestamp of stage 6.

> The manifest's top-level `schema_config_version` (post-A4) records the
> schema_config's `version:` field at the time of bundle build. The
> `files["schema_config.yaml"]["sha256"]` already detects byte-level drift;
> the explicit version answers the human-readable "what was this built
> against?" without re-opening the YAML. Existing manifests without the
> field continue to load — readers MUST tolerate its absence.

---

## Files MapForge does not write

These artifacts live in the bundle directory because the contributor put them
there, not because MapForge generated them:

- **`<source file>`** — the original input. The mapper writes its path into
  `mapping.source.path`; the resolver and emitter read it from that path. You
  decide whether to copy the source into the bundle or reference it from
  outside.
- **`contributor.yaml`** — the contributor's manifest. See
  [`routing-policy.md#contributoryaml`](routing-policy.md#contributoryaml).
- **`routing-policy.yaml`** — the routing policy. Same file content stage 4
  reads from `--policy`, often copied into the bundle for reproducibility.

## Re-running stages

Every stage overwrites its own outputs idempotently. Re-running `mapforge map`
replaces `mapping.yaml`; re-running `mapforge emit` rewrites the entire
`cypher/` directory and the `manifest.json` (which then needs another
`approve`). Earlier stages' outputs are untouched.
