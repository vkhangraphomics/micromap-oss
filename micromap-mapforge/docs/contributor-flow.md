# Contributor Flow

This document walks through the seven-stage MapForge pipeline end-to-end, with
inputs, outputs, failure modes, and debugging tips for each stage.

**Audience:** an external contributor who wants to add a new data source to a
MicroMap-compatible knowledge graph.

**Mental model:** MapForge is a sequence of pure-function transformations over
a directory called the *bundle*. Each stage reads bundle artifacts that earlier
stages wrote and adds its own artifact. Any stage can be re-run idempotently
against the same bundle.

```
inspect → map → resolve → plan → emit → approve → submit
   1       2       3       4      5        6         7
```

| Stage | Reads | Writes |
|---|---|---|
| 1. inspect | the source file | `inspection-report.md`, `inspection.json` |
| 2. map | the source file (heuristic) **or** source + columns (LLM) | `mapping.yaml` |
| 3. resolve | `mapping.yaml`, target Neo4j | `resolution.json`, `unresolved.md` |
| 4. plan | `mapping.yaml`, contributor.yaml, routing-policy.yaml | `routing.yaml` |
| 5. emit | `mapping.yaml`, `routing.yaml`, `resolution.json` | `cypher/*.cypher`, `cypher/*.params.json`, `INGEST_REPORT.md`, `manifest.json` |
| 6. approve | `manifest.json` | `manifest.json` (with `approved=true` + reviewer) |
| 7. submit | bundle + target Neo4j credentials | data + relationships + `:Contribution` in Neo4j |

A complete bundle directory after stage 6 contains:

```
my-bundle/
├── inspection-report.md      ← stage 1 (human-readable)
├── inspection.json           ← stage 1 (machine-readable)
├── mapping.yaml              ← stage 2 (the central artifact)
├── resolution.json           ← stage 3 (resolver output)
├── unresolved.md             ← stage 3 (humans look here when terms don't resolve)
├── routing.yaml              ← stage 4 (destination + provenance config)
├── cypher/                   ← stage 5
│   ├── nodes_<Label>.cypher
│   ├── nodes_<Label>.params.json
│   ├── rels_<TYPE>.cypher
│   └── rels_<TYPE>.params.json
├── INGEST_REPORT.md          ← stage 5 (reviewer's main document)
├── manifest.json             ← stage 5/6 (file hashes + approval state)
└── <your source file>        ← original input, copied in by you
```

See [`bundle-anatomy.md`](bundle-anatomy.md) for field-by-field documentation.

---

## Stage 1 — inspect

**Purpose:** profile the source file. Infer column types, null rates, distinct
counts, and sample values. The reviewer reads `inspection-report.md` to
understand what they're about to map.

**Command:**

```bash
mapforge inspect <SOURCE> --out <BUNDLE_DIR>
```

**Inputs:** a single file (CSV, TSV, JSON, JSONL, Parquet, or SQL dump).
Extension drives dispatch. SQL dumps are parsed as multi-row `INSERT VALUES`
statements.

**Outputs:**
- `inspection-report.md` — markdown table: name, inferred type, null rate,
  distinct count, three sample values per column.
- `inspection.json` — same content, machine-readable.

**Failure modes:**
- Unsupported file extension → exit 2, "no inspector for extension `.xyz`".
- File parse error (malformed CSV, broken JSON) → exit 2, with the line/offset
  of the failure.

**Debugging:** if column types look wrong, check the sample values — the
type inference rule is conservative ("anything non-numeric → string").
Re-run after cleaning the source.

---

## Stage 2 — map

**Purpose:** draft `mapping.yaml` — the column → ontology mapping that drives
every later stage. Two modes:

- **Heuristic (default):** offline, column-name pattern matching. Fast, deterministic, produces a mapping with entities but **empty relationships**.
- **LLM:** Anthropic-API-assisted proposal. Reads the inspection report + your `--hint`, proposes labels, match fields, columns, and relationships. Requires `ANTHROPIC_API_KEY`.

> **Which one, and why?** See [`mapping-quality-evals.md`](mapping-quality-evals.md) —
> both modes scored against curated gold. Short version: the heuristic ties the LLM on
> entity recovery (1.00) for free and offline, so it's the default; reach for the LLM when
> you need **relationships** (heuristic recall 0.00 vs LLM 0.75–1.00) or a messy source
> resists the column hints. Review either before you submit.

**Command:**

```bash
mapforge map <SOURCE> --out <BUNDLE_DIR>                       # heuristic
mapforge map <SOURCE> --out <BUNDLE_DIR> --mode llm \
    --hint "rows are microbiome-disease associations from primary literature"
```

**Inputs:** the same source file. The mapper does not need previous stages —
you can skip inspect and run map directly.

**Outputs:** `mapping.yaml`. Structure documented in [`bundle-anatomy.md#mappingyaml`](bundle-anatomy.md#mappingyaml).

**Heuristic mode caveats:**
- Produces `confidence: INFERRED` on every entity (vs. `EXTRACTED` for hand-curated mappings).
- Produces **`relationships: []`** every time. Relationship inference needs domain context the heuristic doesn't have. If you need relationships and your data source is in the strict-mode label set, either use `--mode llm` or hand-author them after the heuristic draft.

**Hand-authoring:** for production sources, treat the generated `mapping.yaml`
as a starting point. The shipped `examples/disbiome/mapping.yaml` is a
hand-curated reference.

**Failure modes:**
- `ANTHROPIC_API_KEY` unset under `--mode llm` → exit 1, "ANTHROPIC_API_KEY required".
- Schema validation failure → exit 2 with the JSON Schema error path. Common offenders: a label outside the strict-mode enum, missing `match_on`, or relationships in the wrong shape.

---

## Stage 3 — resolve

**Purpose:** for every source row, look up each entity's source term against
the target Neo4j. Three outcomes per entity instance: **resolved** (one match
found), **unresolved** (no candidates), or **ambiguous** (multiple candidates
with no clear winner).

**Command:**

```bash
mapforge resolve --bundle <BUNDLE_DIR> \
    --neo4j-uri bolt://<host>:7687 \
    --neo4j-user <user> --neo4j-password <pw> \
    [--neo4j-database <db>]
```

**Inputs:**
- `<BUNDLE_DIR>/mapping.yaml`.
- The source file at `mapping.source.path` (absolute, or **bundle-relative**).
- A reachable Neo4j 5.x with the target schema's labels and indexed identifier properties.

**Outputs:**
- `resolution.json` — every entity instance + its resolver verdict + candidate IDs.
- `unresolved.md` — human-readable list of source terms that didn't resolve. The reviewer's first stop when counts look low.

**Resolver strategy** (per ontology label):

| Label | Strategy |
|---|---|
| `Disease` | First-match across identifier fields (`doid`, `mesh_id`, `omim_id`, etc.) → fallback to name-normalized lookup using `normalize_disease_name`. |
| `Paper` | Identifier-first (`pmid`, `doi`) → title-fuzzy. |
| All others (`Taxon`, `Metabolite`, `Drug`, `Gene`, `Protein`, `Pathway`, `BodySite`) | `generic.py` — identifier-first lookup, name-fuzzy fallback. |

**Preload optimization (#117):** the resolver only preloads labels the bundle's
mapping actually references — declaring `Taxon`/`Disease`/`Paper` in
`mapping.entities[].label` means we don't query `Gene`/`Protein`/`Pathway`/etc.

**Output interpretation:**
- `resolved_count == row_count × entity_count` is the healthy ceiling.
- Large `unresolved_count` means either (a) source terms don't match anything in your graph (load reference data first?) or (b) your `match_on` field is wrong.
- Large `ambiguous_count` means your graph has duplicates the resolver can't distinguish. `unresolved.md` shows the competing candidates.

**Failure modes:**
- `FileNotFoundError` on the source — `source.path` is relative and CWD-relative interpretation failed. After [#116](https://github.com/vkhangraphomics/MicroMap/issues/116) the resolver anchors relative paths to the bundle dir, so this should only happen if the source file is genuinely missing.
- Neo4j connect/auth errors — `neo4j` driver exceptions surface directly. Check `--neo4j-uri` / credentials / database name.

---

## Stage 4 — plan

**Purpose:** apply the routing policy. Choose which destination the bundle's
submit will write to (`micromap-core`, `registry-only`, or
`new-federated-instance`). Stamp the bundle with `organization_id`,
contributor metadata, and provenance configuration.

**Command:**

```bash
mapforge plan --bundle <BUNDLE_DIR> \
    --organization-id <ID> \
    [--policy <ROUTING_POLICY.YAML>] \
    [--contributor <CONTRIBUTOR.YAML>] \
    [--destination <OVERRIDE>] \
    [--tier <internal|partner|external>] \
    [--sensitivity <public|internal|pii>] \
    [--row-count <N>]
```

**Inputs:**
- `<BUNDLE_DIR>/mapping.yaml` (read for `source.format` and to seed
  `provenance.mapping_version`).
- An optional routing-policy file. If omitted, the default is
  `destination: micromap-core` with no rules.
- An optional contributor manifest. Defaults to
  `<BUNDLE_DIR>/contributor.yaml` if present.

**Outputs:** `routing.yaml`. Key fields:
- `destination: micromap-core | registry-only | new-federated-instance`
- `organization_id`: multi-tenant isolation tag stamped onto every node/relationship written.
- `provenance.contributor` / `submitted_at` / `mapping_version` — provenance metadata.
- `provenance.enabled` (optional, defaults to `true`) — opt-out switch for the Contribution writer. See [`provenance.md`](provenance.md).
- `destinations.new-federated-instance.federation` block — present only when the destination is `new-federated-instance`. See [`new-instance-executor-contract.md`](new-instance-executor-contract.md).

**Decision precedence:**

1. `--destination` (explicit override, highest precedence).
2. First matching rule in `routing-policy.yaml`.
3. `default.destination`.

**Failure modes:**
- Contributor manifest fails schema validation → exit 2. The schema is strict (`additionalProperties: false`); see [`routing-policy.md#contributoryaml`](routing-policy.md#contributoryaml).
- Routing policy fails schema validation → exit 2 with JSON Schema path.
- Destination is `new-federated-instance` but the policy lacks the corresponding `destinations.<name>.federation` block → exit 2 with the missing-field name.

---

## Stage 5 — emit

**Purpose:** generate the parameterized Cypher that submit will run. Build
INGEST_REPORT.md (the reviewer's main document) and manifest.json (file
hashes + approval state).

**Command:**

```bash
mapforge emit --bundle <BUNDLE_DIR>
```

**Inputs:** mapping.yaml + routing.yaml + resolution.json + source file. No
Neo4j credentials needed — emit is offline.

**Outputs:**
- `cypher/nodes_<Label>.cypher` + `cypher/nodes_<Label>.params.json` for each entity label.
- `cypher/rels_<TYPE>.cypher` + `cypher/rels_<TYPE>.params.json` for each relationship type.
- `INGEST_REPORT.md` — resolution summary, confidence breakdown, what the bundle will write.
- `manifest.json` — sha256 hash + size for every file in the bundle. The reviewer's tamper-evident record.

**Cypher shape:** UNWIND batches of parameterized rows, MERGE with `organization_id`-scoped keys. Relationships use `MATCH` against resolved entities (not org-scoped MERGE — that's the #90 fix). See [`cypher-emitter-format.md`](cypher-emitter-format.md) for the full format reference.

**Failure modes:**
- Missing `routing.yaml` → exit 2 "run 'mapforge plan' first".
- Missing `resolution.json` → exit 2 "run 'mapforge resolve' first".
- Source file missing → exit 2.

---

## Stage 6 — approve

**Purpose:** record reviewer sign-off in `manifest.json`. Submit refuses to run
without it (unless you pass `--force`).

**Command:**

```bash
mapforge approve --bundle <BUNDLE_DIR> --reviewer <NAME>
```

**Inputs:** `manifest.json`.

**Outputs:** `manifest.json` is updated:
```json
{
  "approved": true,
  "approved_at": "2026-06-04T23:45:56.050963+00:00",
  "reviewer": "alice",
  ...
}
```

The reviewer field flows into the `:Contribution` provenance node on submit.

**Failure modes:** the only failure is missing `manifest.json` (exit 4). Approve never validates the bundle's substance — it's a record-keeping step.

**Tip:** for production sources, the reviewer is a different person from the
author. The manifest's file hashes give the reviewer a tamper-evident record:
if the bundle changes after approve, submit's `--force` is the only way to push it.

---

## Stage 7 — submit

**Purpose:** execute the bundle's Cypher against the destination, then write
the `:Contribution` provenance node (unless opted out).

**Command:**

```bash
mapforge submit --bundle <BUNDLE_DIR> \
    --neo4j-uri bolt://<host>:7687 \
    --neo4j-user <user> --neo4j-password <pw> \
    [--neo4j-database <db>] \
    [--reviewer <NAME>] \
    [--force]
```

**Inputs:** the full bundle directory, plus credentials for the destination's Neo4j (for the `micromap-core` / `new-federated-instance` paths) or for the central federation registry (for `registry-only`).

**Outputs:**
- Data nodes + relationships written to the destination Neo4j.
- One `:Contribution` provenance node + linked `:Organization` + `:Reviewer` (unless `routing.provenance.enabled` is `false`).
- A `:FederatedSource` registered on micromap-core's registry (only for `new-federated-instance`).

**Pre-flight checks:**
- `manifest.json` exists and `approved: true` (or `--force`).
- `--reviewer` provided, OR the manifest carries a reviewer, OR `provenance.enabled` is `false`.
- All three of `--neo4j-uri`/`--neo4j-user`/`--neo4j-password` provided.

**Per-destination behavior:**

| Destination | What submit does |
|---|---|
| `micromap-core` | Runs the bundle's Cypher directly against the configured bolt URI. The simplest path. |
| `registry-only` | Doesn't write any data nodes. Useful for federated sources that maintain their own loader path — MapForge only registers the source on the central federation registry. |
| `new-federated-instance` | Writes data nodes to the **remote** instance's Neo4j (via bolt), then registers the source on micromap-core's federation registry, then probes the remote to gate the receipt's `success` flag. See [`new-instance-executor-contract.md`](new-instance-executor-contract.md) for the full contract. |

**Provenance writer:** when `routing.provenance.enabled` is `true` (the default),
submit follows the data write with a `:Contribution` provenance node. The
node records contributor org, reviewer, source-file sha256, mapping sha256,
destination string, submit timestamp, and resolved/unresolved/ambiguous counts.
See [`provenance.md`](provenance.md).

When `--force` is used to bypass an unapproved bundle's gate, the
resulting `:Contribution` node is stamped `force_submitted: true` so
auditors can find the bypass later. See
[`provenance.md`](provenance.md) §"Governance properties" for the
audit query. The flag's runtime behavior is unchanged — only the
audit trail differs.

**Failure modes:** every stage exit code is documented in
[`new-instance-executor-contract.md#failure-modes`](new-instance-executor-contract.md#failure-modes)
and the matching block at the bottom of [`provenance.md`](provenance.md).

---

## Stage 7b — submit over HTTP (external self-serve)

External contributors who cannot open a Bolt connection to a constituent Neo4j
submit the **approved** bundle to the hub over HTTP instead. The hub writes it
(with provenance) into the tenant's hub-hosted constituent using server-held
credentials — the client never opens Bolt.

```bash
tar czf bundle.tgz -C <BUNDLE_DIR> .
curl -sS -X POST https://<hub>/api/v1/federation/contributions \
  -H "X-API-Key: <your federation token>" \
  -F "bundle=@bundle.tgz"
```

The endpoint authenticates the token, verifies the bundle is approved and its
`organization_id` matches the token's tenant, validates the emitted Cypher
against an allowlist, and runs the same write + `:Contribution` provenance the
Bolt `submit` produces (parity is enforced by a shared core). `--force` is not
available over HTTP — external contributions must be approved. See
`docs/specs/2026-06-11-mapforge-http-contribution-path-design.md`.

---

## End-to-end example

The shipped Disbiome example is a known-good walkthrough:
[`examples/disbiome/README.md`](../../examples/disbiome/README.md). It runs in
under five minutes against a local Neo4j and lands 106 nodes + 100
relationships + the full provenance triple.

## Curation guideline — populate license + contact + accessed_at

`mapping.yaml::source` accepts 7 optional E3 source-attribution fields.
None is schema-required. **Customers shipping data to a shared
knowledge graph should populate `license`, `contact`, and
`accessed_at` at minimum:**

- **`license`** — redistribution rights. Downstream consumers can't
  legally re-export your data without it. SPDX identifiers
  (`CC-BY-4.0`, `MIT`, etc.) are strongly recommended.
- **`contact`** — lets downstream consumers ask questions. An alias
  email (`help@reactome.org`) is fine; a personal address is fine.
- **`accessed_at`** — pins the dataset snapshot date. Reproducibility
  for time-varying sources (Reactome quarterly drops, OpenTargets
  releases) depends on this.

`ethics_ref` is required only when the source data has IRB or ethics-
review constraints. `url`, `doi`, and `version` are recommended for
discoverability and citation.

The fields are author-edited AFTER `mapforge map` runs (the mapper
generates `name`/`format`/`path`/`sha256` automatically; attribution
isn't inferrable from source bytes). The recommended flow:

```bash
mapforge map ./data.csv --out ./bundle/ --schema-config genomics
# Edit ./bundle/mapping.yaml to add source.license, source.contact, etc.
mapforge resolve ./bundle/ --neo4j-uri bolt://...
mapforge plan ./bundle/
mapforge emit ./bundle/
mapforge approve ./bundle/ --reviewer alice
mapforge submit ./bundle/ --neo4j-uri bolt://...
```

Editing `mapping.yaml` after map changes its hash; the bundle manifest
recomputes the hash automatically when `mapforge emit` runs.

See [`docs/schemas/mapping-yaml.md`](schemas/mapping-yaml.md#source-attribution-e3-optional)
for field constraints and a worked Reactome v89 example.

## Common workflows

**Re-running a stage after fixing a bug:** every stage is idempotent against
the same bundle directory. After fixing your mapping, re-run `resolve` →
`plan` → `emit` and the bundle's earlier artifacts get overwritten in place.
Submit is also idempotent thanks to `MERGE` semantics.

**Rolling back a submission:** see [`provenance.md#rollback`](provenance.md#rollback).

**Routing the same source to different destinations:** edit
`routing-policy.yaml` and re-run `plan` → `emit`. The bundle gets a new
`routing.yaml` with the new destination; nothing else changes.

**Working without a routing policy:** the default behavior is
`destination: micromap-core`. For a quick local test against a single Neo4j
you can skip the `--policy` flag entirely.
