# MicroMap MapForge

**A reviewable ingestion pipeline for biomedical knowledge graphs.**

MapForge turns raw tabular data (CSV, TSV, JSON, JSONL, Parquet, SQL dumps) into
parameterized Cypher bundles that a reviewer can approve before any node lands
in Neo4j. It was built for MicroMap — Graphomics' microbiome knowledge graph —
but the engine is schema-aware, BioCypher-compatible, and works against any
Neo4j 5.x target.

## When to use MapForge

- You have a tabular biomedical data source (rows of taxa-disease associations,
  metabolites, drug targets, paper citations, etc.) and want it in your
  knowledge graph.
- You want **a human review step between extraction and write** — you want to
  see exactly which entities will be MERGEd, which relationships will be
  created, and which source terms didn't resolve, *before* anything touches
  Neo4j.
- You want bundles to be **deterministic and inspectable** — the same input
  produces the same Cypher output, and the output is plain-text and
  diff-friendly.
- You want **provenance and auditability** by default — every successful
  submission records who contributed, who approved, source-file hashes, and
  resolved/unresolved counts in the target graph (or, for stand-alone curated
  KGs, you opt out of that).

## What MapForge isn't

- It's not a graph database. It writes to one (Neo4j 5.x).
- It's not a continuous-ingest stream processor. The unit of work is the
  *bundle* — one source, one mapping, one review, one submit.
- It's not a generic ETL framework. The mapping schema's strict mode constrains
  entity labels to the MicroMap ontology (`Taxon`, `Disease`, `Metabolite`,
  `Drug`, `Gene`, `Protein`, `Pathway`, `Paper`, `BodySite`). For non-MicroMap
  KGs, see the BioCypher adapter path (`mapforge import-kg`).

## Install

Three install paths are supported. Pick whichever fits your environment.

### pip (canonical)

```bash
pip install -e ".[dev]"
```

### Docker

```bash
docker build -t mapforge:dev .                          # from micromap-mapforge/
docker run --rm mapforge:dev --help
```

The image's WORKDIR is `/work`. Mount your bundle there and run any
subcommand:

```bash
docker run --rm -v $(pwd)/bundle:/work mapforge:dev \
    resolve --bundle /work \
    --neo4j-uri bolt://host.docker.internal:7687 \
    --neo4j-user neo4j --neo4j-password test
```

(Linux hosts can use `--network=host` plus `bolt://localhost:7687` if
`host.docker.internal` isn't available.)

### conda

```bash
conda env create -f environment.yml
conda activate mapforge
pip install -e .
```

Conda is supported as an alternative — the canonical install path is pip.
When `pyproject.toml` deps change, `environment.yml` is updated in the same
commit.

### Prereqs

- Python 3.11 or 3.12 (3.13 is untested as of 2026-06; see compat matrix).
- A reachable Neo4j 5.x (local Docker is fine — `docker run --rm -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/test neo4j:5.15`).
- Optionally `ANTHROPIC_API_KEY` for the LLM-assisted mapper (`mapforge map --mode llm`).

Verify the install:

```bash
mapforge --help
```

## Compatibility matrix

| Component | Version | Where pinned |
|---|---|---|
| Python | ≥ 3.11, < 3.13 (3.13 untested) | `pyproject.toml::requires-python` |
| Neo4j Python driver | ≥ 5.15, < 7 | `pyproject.toml::dependencies` |
| Neo4j server | 5.x — tested against 5.15 in CI | testcontainers fixture in `tests/` |
| BioCypher | ≥ 0.15.1 | `pyproject.toml::dependencies` (used by `mapforge import-kg`) |
| Anthropic SDK | ≥ 0.40.0 | `pyproject.toml::dependencies` (used by `mapforge map --mode llm`) |
| PyArrow | ≥ 15.0.0 | `pyproject.toml::dependencies` (used by the Parquet inspector) |

**Driver-server compatibility:** Neo4j 5.x drivers connect to Neo4j 5.x
servers. Neo4j 4.x is not supported — the driver pin `<7` is forward-only,
and the Cypher emitter uses 5.x-specific features (`UNWIND`-batched MERGE
patterns are 5.x-compatible but the generated Cypher hasn't been validated
against 4.x).

**Operating systems:** developed and tested on Linux (CI) and Windows
(developer machines). macOS should work but isn't CI-tested.

**Docker base:** `python:3.11-slim` (Debian-derived). The Dockerfile uses
build-time gcc/g++ for any wheel that lacks a manylinux binary, then
removes them in the same layer.

## 30-second quick start

The shipped Disbiome example is the canonical walkthrough. From the repo root:

**Optional: see what discipline templates are baked in.**

```bash
mapforge templates list
```

Output (3-column format: name, version, description):

```
genomics         v1.0.0  MicroMap genomics KG ontology (Gene/Variant/Disease/Phenotype/Pathway/etc.)
microbiome       v1.0.0  MicroMap microbiome KG ontology (Taxon/Disease/Metabolite/etc.)
transcriptomics  v1.0.0  MicroMap transcriptomics KG ontology (Gene/Sample/Condition/Tissue/CellType/etc.)
```

For just the version of a single template:

```bash
mapforge templates version genomics
# → 1.0.0
```

For structured (JSON) output for scripting:

```bash
mapforge templates list --json
```

To see what changed between two versions of a built-in template (templates
are versioned in place, so an older body is resolved from git history rather
than a shipped archive):

```bash
mapforge templates diff genomics --from 1.0.0 --to 1.1.0
```

Pick the one closest to your data — `microbiome`, `genomics`, or
`transcriptomics`. Pass its name to `--schema-config` below. Omitting
`--schema-config` defaults to `microbiome`.

See [`docs/schemas/discipline-templates.md`](docs/schemas/discipline-templates.md)
for the full list and per-template class/relationship tables.

```bash
# Start a local Neo4j (Docker)
docker run --rm --name micromap-neo4j -d \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/test neo4j:5.15

# Inspect → map → resolve → plan → emit → approve → submit
mapforge inspect examples/disbiome/disbiome_sample.csv --out /tmp/bundle
cp examples/disbiome/mapping.yaml /tmp/bundle/
cp examples/disbiome/disbiome_sample.csv /tmp/bundle/
cp examples/disbiome/routing-policy.yaml /tmp/bundle/
cp examples/disbiome/contributor.yaml /tmp/bundle/

mapforge resolve --bundle /tmp/bundle \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j --neo4j-password test

mapforge plan --bundle /tmp/bundle \
  --organization-id disbiome-demo \
  --policy /tmp/bundle/routing-policy.yaml \
  --contributor /tmp/bundle/contributor.yaml

mapforge emit --bundle /tmp/bundle
mapforge approve --bundle /tmp/bundle --reviewer your-name

mapforge submit --bundle /tmp/bundle \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j --neo4j-password test
```

That writes 106 nodes (`:Taxon`, `:Disease`, `:Paper`) plus 100 relationships
(`ASSOCIATED_WITH_DISEASE`, `MENTIONED_IN`) plus a `:Contribution` provenance
node to your Neo4j. See [`examples/disbiome/README.md`](../examples/disbiome/README.md)
for the full walkthrough with output transcripts.

## CLI reference

| Command | Stage | Purpose |
|---|---|---|
| `mapforge inspect <SOURCE> --out DIR` | 1 | Profile a source file. Emits `inspection-report.md` + `inspection.json`. |
| `mapforge map <SOURCE> --out DIR [--mode heuristic\|llm] [--hint TEXT]` | 2 | Draft a column → ontology mapping. Emits `mapping.yaml`. |
| `mapforge resolve --bundle DIR --neo4j-uri ... --neo4j-user ... --neo4j-password ...` | 3 | Look up source terms against the target graph. Emits `resolution.json` + `unresolved.md`. |
| `mapforge plan --bundle DIR --organization-id ID [--policy FILE] [--contributor FILE]` | 4 | Apply the routing policy. Emits `routing.yaml`. |
| `mapforge emit --bundle DIR` | 5 | Generate parameterized Cypher. Emits `cypher/*.cypher` + `cypher/*.params.json` + `INGEST_REPORT.md` + `manifest.json`. |
| `mapforge approve --bundle DIR --reviewer NAME` | 6 | Record the reviewer in `manifest.json` and mark the bundle approved for submit. |
| `mapforge submit --bundle DIR --neo4j-uri ... --neo4j-user ... --neo4j-password ... [--reviewer NAME]` | 7 | Execute the bundle's Cypher against the destination. Writes the data + provenance. |
| `mapforge import-kg <ADAPTER_PATH> --source FILE --organization-id ID --out DIR [--schema-mode adopt\|adapt]` | (alt) | BioCypher adapter path for non-MicroMap KGs. See below and the design spec. |
| `mapforge templates list [--json]` / `templates version NAME` / `templates diff NAME --from V --to V` | (util) | Inspect and diff the built-in discipline templates. See the quick-start section above. |

Every command supports `--help`. Run any command with no arguments to see its full option list.

### `import-kg`: BioCypher adapters for non-MicroMap knowledge graphs

`import-kg` takes a Python object implementing the `AdapterProtocol`
(`name`, `schema_config`, `get_nodes()`, `get_edges()`) and produces the same
kind of contribution bundle the 7-stage flow does — `mapping.yaml`,
`routing.yaml`, `cypher/*.cypher` + `*.params.json`, `manifest.json` — ready
for `approve`/`submit`. Shipped adapters live under
`micromap_mapforge/integration/biocypher/adapters/`:

| Adapter | Source | Notes |
|---|---|---|
| `OpenTargetsAdapter` | Open Targets (parquet) | Target→Gene/Disease, Molecule→Drug, `ASSOCIATED_WITH_DISEASE` edges |
| `PrimeKGAdapter` | PrimeKG (`nodes.tab` + `edges.csv`) | ~129K nodes / ~8.1M edges at full scale; excludes CTD-sourced `exposure` nodes by default (commercial-use-restricted license) |

Two schema modes control how an adapter's own node labels map onto the
destination graph:

- `--schema-mode=adopt` (default) — the adapter's labels are used as-is.
  Every shipped adapter above uses this mode.
- `--schema-mode=adapt` — remap an adapter's labels via each node's
  `input_label`, for cases where the adapter's vocabulary needs to land on
  this project's canonical labels instead of its own.

`import-kg` streams: nodes and edges are read from the adapter and written
to the bundle incrementally rather than held in memory, so it scales to
multi-million-row sources — proven end-to-end against PrimeKG's real ~129K
nodes / ~8.1M edges (writes ~128.5K nodes / ~8.1M edges after the license
exclusion, in under 9 minutes with flat memory use).

## Documentation

### Guides (how to)

| Topic | Doc |
|---|---|
| Step-by-step walkthrough of the 7-stage flow | [`docs/contributor-flow.md`](docs/contributor-flow.md) |
| What's inside a bundle directory | [`docs/bundle-anatomy.md`](docs/bundle-anatomy.md) |
| Format of the Cypher emitter output | [`docs/cypher-emitter-format.md`](docs/cypher-emitter-format.md) |
| Authoring `routing-policy.yaml` | [`docs/routing-policy.md`](docs/routing-policy.md) |
| Provenance: what gets recorded, how to audit, how to opt out | [`docs/provenance.md`](docs/provenance.md) |
| Federated-instance routing (write to a remote MicroMap) | [`docs/new-instance-executor-contract.md`](docs/new-instance-executor-contract.md) |
| Worked example with command transcripts | [`examples/disbiome/README.md`](../examples/disbiome/README.md) |

### Schema reference (field-by-field)

| Schema | Doc |
|---|---|
| `mapping.yaml` — source → ontology mapping | [`docs/schemas/mapping-yaml.md`](docs/schemas/mapping-yaml.md) |
| `routing-policy.yaml` — routing rules + provenance + federation | [`docs/schemas/routing-policy-yaml.md`](docs/schemas/routing-policy-yaml.md) |
| `contributor.yaml` — contributor manifest | [`docs/schemas/contributor-yaml.md`](docs/schemas/contributor-yaml.md) |
| MicroMap ontology asset (labels, identifiers, relationships) | [`docs/schemas/ontology.md`](docs/schemas/ontology.md) |
| `normalize.py` — disease-name normalization + disease-ID rules | [`docs/schemas/normalize.md`](docs/schemas/normalize.md) |

## Architecture in one diagram

```
       ┌─────────────┐
SOURCE │  raw data   │  CSV / TSV / JSON / JSONL / Parquet / SQL dump
       └──────┬──────┘
              │
              ▼
       ┌─────────────────────────────────────────────────────────────┐
       │  1. inspect   profile columns, infer types, sample values    │
       │  2. map       columns → ontology entities + relationships    │
       │  3. resolve   look up source terms against the target graph  │
       │  4. plan      apply routing policy → pick destination        │
       │  5. emit      generate parameterized Cypher + INGEST_REPORT  │
       │  6. approve   reviewer signs off on manifest                 │
       └──────┬──────────────────────────────────────────────────────┘
              │ bundle/  (mapping.yaml, routing.yaml, resolution.json,
              │           cypher/*.cypher, manifest.json, …)
              ▼
       ┌─────────────────────────────────────────────────────────────┐
       │  7. submit    execute Cypher → Neo4j                         │
       │               write Contribution provenance (or skip if      │
       │               provenance.enabled=false)                      │
       └──────┬──────────────────────────────────────────────────────┘
              │
              ▼
       ┌─────────────┐
       │   Neo4j     │  destination: micromap-core | new-federated-instance | registry-only
       └─────────────┘
```

The bundle directory is the **stable contract** between every stage — each
stage reads what previous stages wrote and adds its own artifact, and any stage
can be re-run independently against the same bundle.

## Citing MapForge

If you use MapForge in academic work, please cite us. See [`CITATION.cff`](CITATION.cff).

## License

[TBD — tracked under MAC-56.] Bundled reference data sources retain their
upstream licenses; see [`docs/citation-and-licensing.md`](docs/citation-and-licensing.md).

## Status

MapForge is in active development. The 7-stage pipeline is shipped (M1-M3 ✓);
federated-instance write path is shipped (#58 ✓); BioCypher integration is
shipped (#101 ✓). Strategic roadmap is tracked under the
[MAC-35 epic](https://graphomics.atlassian.net/browse/MAC-35) and mirrored as
the [GH#74-#79 theme issues](https://github.com/vkhangraphomics/MicroMap/issues?q=is%3Aissue+is%3Aopen+label%3Aenhancement).
