# `mapping.yaml` Schema Reference

Field-by-field reference for the central artifact of a MapForge bundle.
For the prose authoring guide, see [`../contributor-flow.md#stage-2--map`](../contributor-flow.md#stage-2--map);
for higher-level bundle context, see [`../bundle-anatomy.md`](../bundle-anatomy.md).

**JSON Schema:** [`micromap_mapforge/mapping/mapping.schema.json`](../../micromap_mapforge/mapping/mapping.schema.json) (Draft 2020-12, strict).

## Top-level shape

```yaml
source:        { ... }     # required
entities:      [ ... ]     # required, ≥1 item
relationships: [ ... ]     # required, may be empty
```

`additionalProperties: false` at every level. Any key not listed below
fails validation with a clear error path.

---

## `source` (required)

Where the row data comes from.

```yaml
source:
  name: string             # required, non-empty
  format: enum             # required
  path: string             # required, non-empty
  description: string      # optional
```

| Field | Type | Constraints | Description |
|---|---|---|---|
| `name` | string | min length 1 | Human-readable source identifier. Free-form. |
| `format` | enum | `csv`, `tsv`, `json`, `jsonl`, `parquet`, `sql_dump`, `schema_adapter` | Drives inspector dispatch and decides which entity-mapping mode is in force (see [Strict vs permissive mode](#strict-vs-permissive-mode) below). |
| `path` | string | min length 1 | Path to the source file. Relative paths are anchored to the bundle directory at resolve/emit time ([fixed in #116](https://github.com/vkhangraphomics/graphomics-kg/issues/116)). Absolute paths are passed through unchanged. |
| `description` | string | optional, free-form | Multi-line description; commonly used to record source license, citation, or curation notes. |

**Worked example:**

```yaml
source:
  name: "disbiome_curated_example"
  format: csv
  path: "disbiome_sample.csv"
  description: >
    Curated microbiome-disease associations from primary literature.
    Each row carries pmid + doi pointing at the original publication.
```

### Source attribution (E3, optional)

These 7 optional fields capture dataset attribution for the bundle's
`:Contribution` provenance node. None is required; populating
`license` + `contact` + `accessed_at` is recommended for any source
contributed to a shared knowledge graph.

| Field | Type | Constraint | Description |
|---|---|---|---|
| `license` | string | free-form | SPDX identifier strongly recommended (e.g. `CC-BY-4.0`, `MIT`, `Apache-2.0`, `CC0-1.0`). Not enforced — `"Apache 2"` validates, but won't join with `"Apache-2.0"` at query time. |
| `url` | string | RFC 3986 URI | Canonical source URL. Format-checked. |
| `doi` | string | `^10\.\d{4,}/[^\s]+$` | Bare DOI, no `doi:` prefix, no URL form. |
| `contact` | string | RFC 5321 email | Email of a person or alias for source-level questions. Format-checked. |
| `version` | string | free-form | Source version (`v89`, `2024-Q4`, `snapshot-2026-06-01`). Distinct from MapForge's bundle version. |
| `accessed_at` | string | `YYYY-MM-DD` | ISO 8601 date when this source snapshot was retrieved. No time/timezone — calendar day only. |
| `ethics_ref` | string | free-form | Local IRB/ethics reference (e.g. `IRB-2024-0428`). Not validated against any registry. |

Worked example — a Reactome v89 bundle:

```yaml
source:
  name: reactome-pathways
  format: tsv
  path: ./reactome.tsv
  sha256: 4bafd07134d07050f785e58d009353cae2db4f81502ce6599c1e1f51f66e8efe
  license: "CC-BY-4.0"
  url: "https://reactome.org/download-data"
  doi: "10.1093/nar/gkx1132"
  contact: "help@reactome.org"
  version: "v89"
  accessed_at: "2026-06-06"
```

These fields thread through `mapforge submit` into properties on the
`:Contribution` provenance node under the prefixed names
`source_license`, `source_url`, etc. See [`docs/provenance.md`](../provenance.md)
for the node schema.

---

## `entities` (required, ≥1 item)

Per-row entity declarations. Each item maps source columns to one ontology
entity instance per row. The **strict vs permissive** rule governs which
shape is allowed.

### Strict mode (`source.format != schema_adapter`)

```yaml
- label:      enum                      # required
  match_on:   string                    # required
  columns:    { ontology_field: column_name, ... }    # required, ≥1 key
  normalizer: string                    # optional
  confidence: enum                      # optional
```

| Field | Type | Constraints |
|---|---|---|
| `label` | enum | `Taxon`, `Disease`, `Metabolite`, `Drug`, `Gene`, `Protein`, `Pathway`, `Paper`, `BodySite` |
| `match_on` | string | min length 1; name of the ontology property the resolver uses for lookup |
| `columns` | object | ≥1 entry; keys are ontology field names, values are source column names |
| `normalizer` | string | optional; the name of a function in [`micromap_mapforge/normalize.py`](../../micromap_mapforge/normalize.py). See [`normalize.md`](normalize.md). |
| `confidence` | enum | optional; `EXTRACTED`, `INFERRED`, or `AMBIGUOUS`. Defaults to `EXTRACTED` if absent. |

**Strict-mode label enum:** the strict label list is hard-coded to MicroMap's
ontology. The supported labels and their identifier/match-key semantics are
listed in [`ontology.md`](ontology.md). For non-MicroMap labels see
[Permissive mode](#permissive-mode-sourceformat--schema_adapter).

**Worked example (Disbiome):**

```yaml
entities:
  - label: Taxon
    match_on: ncbi_tax_id
    columns:
      ncbi_tax_id: ncbi_taxid       # ontology field ← source column
      scientific_name: microorganism
      rank: taxonomic_rank
    confidence: EXTRACTED

  - label: Disease
    match_on: name_normalized
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
```

### Permissive mode (`source.format = schema_adapter`)

When the source format is `schema_adapter` (BioCypher adapter path,
invoked via `mapforge import-kg`), the entity-mapping shape stays the same
but `label` becomes a free-form string. Used by curated KGs whose labels
fall outside MicroMap's ontology.

```yaml
source:
  format: schema_adapter
  ...

entities:
  - label: Movie              # any string is allowed in permissive mode
    match_on: title
    columns:
      title: title
      year: year
```

(Relaxing the strict-mode enum so any format can carry any label is
tracked under [Theme A — #74](https://github.com/vkhangraphomics/MicroMap/issues/74).)

---

## `relationships` (required, may be empty)

Per-row relationship declarations. The list may be empty — a bundle that
only writes nodes (no edges) is valid.

```yaml
- type:       string                    # required
  from:       string                    # required
  to:         string                    # required
  properties: { ... }                   # optional
  confidence: enum                      # optional
```

| Field | Type | Constraints |
|---|---|---|
| `type` | string | min length 1; the Cypher relationship type (uppercase by convention) |
| `from` | string | min length 1; Cypher-fragment of the form `<Label>(<match_field>=<expression>)` |
| `to` | string | min length 1; same shape as `from` |
| `properties` | object | optional; per-field value can be a column name (string) or a constant (`{ constant: <value> }`) |
| `confidence` | enum | optional; same enum as entities |

**`from`/`to` syntax:** the expression can reference any column via
`row.<column_name>` and can compose a normalizer
(`normalize(row.<column_name>)`). The emitter parses these into Cypher
`MATCH` clauses. See [`../cypher-emitter-format.md#relationship-format`](../cypher-emitter-format.md#relationship-format)
for the resulting Cypher.

**Worked example:**

```yaml
relationships:
  - type: ASSOCIATED_WITH_DISEASE
    from: "Taxon(ncbi_tax_id=row.ncbi_taxid)"
    to:   "Disease(name_normalized=normalize(row.disease))"
    properties:
      direction:      qualitative_outcome           # column lookup
      sample_type:    sample
      method:         method
      pmid:           pmid
      doi:            doi
      evidence_level: { constant: literature_curated }   # constant value
    confidence: EXTRACTED

  - type: MENTIONED_IN
    from: "Taxon(ncbi_tax_id=row.ncbi_taxid)"
    to:   "Paper(pmid=row.pmid)"
    properties:
      context: { constant: microbiome_association }
    confidence: EXTRACTED
```

---

## Strict vs permissive mode

The schema's top-level `if/then/else` switches the entity-mapping shape
based on `source.format`:

| `source.format` value | Entity-mapping mode | `label` rule |
|---|---|---|
| `schema_adapter` | Permissive | Any non-empty string |
| Anything else (csv, tsv, json, jsonl, parquet, sql_dump) | Strict | One of the 9-label enum |

Both modes use the same field set for an entity — only the `label` constraint
differs.

---

## Validation behavior

The validator is `Draft202012Validator` with sorted error reporting (see
[`micromap_mapforge/mapping/validator.py`](../../micromap_mapforge/mapping/validator.py)).
Errors are surfaced with a dotted field path so problems are findable in
big mappings:

```
mapping invalid:
  - entities.0.label: 'Movie' is not one of ['Taxon', 'Disease', 'Metabolite', 'Drug', 'Gene', 'Protein', 'Pathway', 'Paper', 'BodySite']
  - relationships.0.from: 'Taxon(ncbi_tax_id)' does not match the from/to Cypher-fragment shape
  - source.format: 'xml' is not one of ['csv', 'tsv', 'json', 'jsonl', 'parquet', 'sql_dump', 'schema_adapter']
```

### Common validation errors

| Error | Cause | Fix |
|---|---|---|
| `source.format: 'xml' is not one of [...]` | An unsupported source format. | Convert to one of the supported formats, or use `schema_adapter` + a BioCypher adapter. |
| `entities.N.label: 'Movie' is not one of [...]` | Non-MicroMap label on a non-`schema_adapter` source. | Switch `source.format` to `schema_adapter` (and provide an adapter), or restructure with MicroMap labels. |
| `entities.N.columns: should be a non-empty object` | Empty `columns: {}` block. | Add at least one `<ontology_field>: <source_column>` mapping. |
| `relationships.N.from/to: should be a string` | Used the rejected dict shape (`{ label: X, on: Y }`). | Use the canonical Cypher-fragment string: `"X(Y=row.<column>)"`. |
| `additional properties are not allowed ('<key>' was unexpected)` | Typo in a field name; the schema is strict. | Check the field-name spelling against this reference. |

---

## Source-of-truth files

| What | Where |
|---|---|
| JSON Schema | [`micromap_mapforge/mapping/mapping.schema.json`](../../micromap_mapforge/mapping/mapping.schema.json) |
| Validator implementation | [`micromap_mapforge/mapping/validator.py`](../../micromap_mapforge/mapping/validator.py) |
| Mapper that drafts these files | [`micromap_mapforge/mapping/mapper.py`](../../micromap_mapforge/mapping/mapper.py) |
| Ontology that constrains the strict-mode labels | [`micromap_mapforge/mapping/ontology.yaml`](../../micromap_mapforge/mapping/ontology.yaml) — rendered in [`ontology.md`](ontology.md) |
| Normalizers | [`micromap_mapforge/normalize.py`](../../micromap_mapforge/normalize.py) — rendered in [`normalize.md`](normalize.md) |

## Cross-references

- Bundle anatomy (where `mapping.yaml` fits in the bundle): [`../bundle-anatomy.md#mappingyaml`](../bundle-anatomy.md#mappingyaml)
- Authoring walkthrough: [`../contributor-flow.md#stage-2--map`](../contributor-flow.md#stage-2--map)
- Generated Cypher this drives: [`../cypher-emitter-format.md`](../cypher-emitter-format.md)
- Worked example in production: [`examples/disbiome/mapping.yaml`](../../../examples/disbiome/mapping.yaml)
