# Discipline templates

MapForge ships curated discipline templates so you can start ingesting
data without authoring a `schema_config.yaml` from scratch. Each template
is a complete LinkML + `x_mapforge` ontology for its discipline, baked
into the package under `micromap_mapforge/mapping/templates/<name>.yaml`.

## Selecting a template

The `mapforge map` subcommand accepts a built-in template name OR a path
to a custom `schema_config.yaml`:

```bash
# Use the baked-in genomics template
mapforge map --schema-config genomics ./data.csv ./bundle/

# Use a custom project schema
mapforge map --schema-config ./my-schema.yaml ./data.csv ./bundle/
```

The selection rule (matches `load_schema_config()`):

1. If the value is an existing file path → load that file.
2. Else if it matches a built-in template name (case-insensitive) → load
   the baked-in template.
3. Else → error listing the available built-ins.

Omitting `--schema-config` falls back to `microbiome` (silent default,
preserved for backward compatibility).

To discover what's available:

```bash
mapforge templates list
mapforge templates show genomics > my-schema.yaml   # to extend
```

### CLI discovery

```bash
$ mapforge templates list
genomics         v1.0.0  MicroMap genomics KG ontology (Gene/Variant/Disease/Phenotype/Pathway/etc.)
microbiome       v1.0.0  MicroMap microbiome KG ontology (Taxon/Disease/Metabolite/etc.)
transcriptomics  v1.0.0  MicroMap transcriptomics KG ontology (Gene/Sample/Condition/Tissue/CellType/etc.)

$ mapforge templates list --json    # structured output for scripting
$ mapforge templates version genomics
1.0.0

$ mapforge templates show genomics > my-schema.yaml
$ # edit my-schema.yaml to extend or override classes
$ mapforge map --schema-config ./my-schema.yaml ./data.csv ./bundle/
```

The `show` subcommand dumps the YAML to stdout — pipe it to a file to
start a project-specific extension. The output is byte-identical to what
ships under `micromap_mapforge/mapping/templates/<name>.yaml`.

## Available templates

| Name | Discipline | Anchor sources | Node classes | Relationships |
|------|-----------|----------------|--------------|---------------|
| `microbiome` | Microbiome-disease associations + producers | NCBI Taxonomy, Disbiome, HMDB, KEGG, DrugBank | 12 | 16 |
| `genomics` | Variant interpretation + pathway/phenotype | ClinVar, gnomAD, OMIM, HGNC, Ensembl, MONDO, HPO, Reactome | 11 | 12 |
| `transcriptomics` | Bulk + single-cell expression + DE | GTEx, ARCHS4, GEO, TCGA, Reactome, GO, UBERON, EFO, CL | 11 | 12 |

(metabolomics and proteomics templates are filed as follow-ups under #74.)

## Curation guidelines

These rules apply to every template, and are the standard any new
template should be reviewed against.

### 1. Disambiguate cross-discipline edges

When the same conceptual edge has different endpoint classes per
discipline, **rename one of them** to be discipline-specific. Example:
the microbiome template's `TARGETS` is `Drug → Protein` (mechanism of
action). Pharmacogenomics needs `Drug → Gene` (clinical actionability).
The genomics template uses `PHARMACO_TARGETS` instead of overloading
`TARGETS` with a different endpoint class — projects loading both
templates get unambiguous relationships.

### 2. Shared concepts, different prefix sets are fine

When the same concept appears in multiple disciplines but the practical
CURIE prefix set differs, **keep the class name shared but let the
`id_prefixes` list diverge**. Example: `Study` is in both genomics
(`[PMID, dbGaP]`) and transcriptomics (`[GEO, PMID, dbGaP]`). Templates
are project-scoped; co-loading two templates in one project is not
currently supported (a Theme B concern).

### 3. Document every non-obvious decision in `x_mapforge.notes:`

If a class's identifier list, primary_id, or merge semantics differ from
the microbiome template's, the class must carry an `x_mapforge.notes:`
block explaining why. Templates are documentation as much as schema — a
contributor reading a template should understand the curation choices
without cross-referencing a separate document.

### 4. CURIE prefixes are Bioregistry-validated at load time

Every prefix referenced in a template (both the top-level `prefixes:` block
and every class's `id_prefixes:` list) is checked against Bioregistry when
`load_schema_config()` runs. The shipped templates use uppercase forms
(`MONDO`, `HGNC`, `NCBIGene`) as the LinkML / life-sciences convention;
canonical lowercase forms (`mondo`, `hgnc`, `ncbigene`) are equally valid.
Either is accepted automatically via Bioregistry's case-insensitive synonym
resolver — use whichever matches the convention of the templates or external
datasets you're aligning with.

For project-internal non-CURIE prefixes (project-local IDs that don't map
to any public registry), use the reserved `local:` escape hatch — already
used by the transcriptomics `Sample` class for IDs like
`local:patient_42_baseline`. No other escape hatch is supported at v1; to
request an additional reserved name, open an issue with the use case.

Unknown prefixes fail load with a one-line error message that names the
offending prefix, where it was found (top-level or which class), and a
"Did you mean 'X'?" suggestion when Bioregistry has a close match. The
implementation lives in `micromap_mapforge/mapping/bioregistry_check.py`.

### 5. `version:` follows semver. `last_updated:` is human-readable.

Every template declares a `version: "MAJOR.MINOR.PATCH"` field, enforced at
load time post-A4 via `validate_version()` in `mapping/schema_config.py`. Bump
rules:

- **MAJOR** — breaking changes for consumers: class removed, class renamed,
  identifier slot removed from `identifiers:` list, `primary_id` changed,
  relationship `subject`/`object` endpoint changed.
- **MINOR** — additive changes: new class, new relationship, new identifier
  in an existing class's `identifiers:` list, new `id_prefix` added to a
  class, new entry in `common_columns_hint`.
- **PATCH** — clarifying changes only: typo fix in a `notes:` block,
  comment rewrite, URL in `prefixes:` updated to a canonical form. No
  behavioral change for any consumer.

A MAJOR bump in any template requires a coordinated bump of mapforge's
`KNOWN_SCHEMA_MAJOR` constant in the same release. The Layer 3 regression
test `test_every_builtin_template_declares_supported_version` catches an
uncoordinated bump at PR time.

The optional `last_updated: "YYYY-MM-DD"` field records the last curation
date for human reference. It is opaque to the loader — not validated, not
read by any consumer. Use it as a "when was this curated" marker
independent of the semver number.

## Template details

### `genomics`

**Scope:** Human germline & somatic variant interpretation + pathway/
phenotype linkage. Anchor sources: ClinVar, gnomAD, OMIM, HGNC, Ensembl,
MONDO, HPO, Reactome, PharmGKB.

**Node classes (11):**

| Class | Primary `id_prefixes` | Merge key | Notes |
|---|---|---|---|
| `Gene` | HGNC, NCBIGene, Ensembl | `gene_id` | Identical shape to transcriptomics Gene. |
| `Variant` | ClinVar, dbSNP, gnomAD, HGVS | `variant_id` | `vcf_coords` is a non-CURIE fallback merge key (sorted chrom + 1-based pos + uppercase ref/alt). |
| `Genome` | RefSeq, Ensembl | `assembly_id` | Anchor for `LOCATED_ON`. Two rows expected (per build). |
| `Transcript` | Ensembl, RefSeq | `transcript_id` | Required for `hgvs_c`. |
| `Protein` | UniProt | `protein_id` | Carried from microbiome. |
| `Disease` | MONDO, OMIM, MESH, DOID, Orphanet | `name_normalized` | Reuses `normalize_disease_name`. ICD10 dropped vs. microbiome. |
| `Phenotype` | HPO | `phenotype_id` | Explicitly separate from Disease. |
| `Pathway` | Reactome, KEGG, WikiPathways | `pathway_id` | Microbiome + WikiPathways. |
| `Drug` | DrugBank, CHEMBL, RxNorm | `drug_id` | Reused from microbiome. |
| `Paper` | PMID, DOI | `paper_id` | Reused. |
| `Study` | PMID, dbGaP | `study_id` | dbGaP for cohort studies. |

**Relationships (12):**

| Edge | Subject → Object | Notable properties |
|---|---|---|
| `LOCATED_IN_GENE` | Variant → Gene | consequence, exon |
| `LOCATED_ON` | Variant → Genome | chrom, pos, build |
| `OCCURS_IN_TRANSCRIPT` | Variant → Transcript | hgvs_c, consequence |
| `ENCODES` | Gene → Protein | — |
| `TRANSLATES_TO` | Transcript → Protein | — |
| `ASSOCIATED_WITH_DISEASE` | Variant → Disease | clinical_significance, review_status, evidence_level |
| `GENE_ASSOCIATED_WITH_DISEASE` | Gene → Disease | inheritance, evidence_level |
| `HAS_PHENOTYPE` | Disease → Phenotype | frequency |
| `PARTICIPATES_IN` | Gene → Pathway | role |
| `PHARMACO_TARGETS` | Drug → Gene | action_type, evidence_level |
| `MENTIONED_IN` | any → Paper | sentence |
| `REPORTED_IN` | Variant → Study | cohort_size, allele_frequency |

Source: [`micromap_mapforge/mapping/templates/genomics.yaml`](../../micromap_mapforge/mapping/templates/genomics.yaml)

### `microbiome`

See the source for the full canonical shape:
[`micromap_mapforge/mapping/templates/microbiome.yaml`](../../micromap_mapforge/mapping/templates/microbiome.yaml).
Curation predates the discipline-templates framework; the template is the
authoritative reference for the microbiome KG.

### `transcriptomics`

**Scope:** Bulk + single-cell expression and differential-expression
analyses, anchored to gene/tissue/condition. Anchor sources: GTEx, ARCHS4,
GEO, TCGA, Reactome, GO, UBERON, EFO, CL.

**Node classes (11):**

| Class | Primary `id_prefixes` | Merge key | Notes |
|---|---|---|---|
| `Gene` | HGNC, NCBIGene, Ensembl | `gene_id` | Identical shape to genomics Gene. |
| `Transcript` | Ensembl, RefSeq | `transcript_id` | Identical shape to genomics. |
| `Sample` | GEO, insdc.sra, local | `sample_id` | `local:` prefix for project-internal IDs. SRA renamed to canonical Bioregistry `insdc.sra` post-A3'. |
| `Study` | GEO, PMID, dbGaP | `study_id` | Same class name as genomics Study, GEO Series added. |
| `Condition` | EFO, MONDO | `condition_id` | Experimental contrast / exposure. `label` slot for free-text. |
| `Tissue` | UBERON, BTO | `tissue_id` | Distinct from microbiome BodySite (different relationship context). |
| `CellType` | CL, CLO | `celltype_id` | Required for single-cell work. |
| `Pathway` | Reactome, KEGG, WikiPathways, GO | `pathway_id` | Genomics Pathway + GO terms. |
| `Disease` | MONDO, OMIM, MESH, DOID, EFO | `name_normalized` | Genomics Disease + EFO. |
| `Paper` | PMID, DOI | `paper_id` | Reused. |
| `Protein` | UniProt | `protein_id` | Minimal — target of TRANSLATES_TO. |

**Relationships (12):**

| Edge | Subject → Object | Notable properties |
|---|---|---|
| `EXPRESSED_IN` | Gene → Sample | tpm, count, normalized_value, method |
| `DIFFERENTIALLY_EXPRESSED_IN` | Gene → Condition | log2_fc, p_value, q_value, direction, contrast |
| `SAMPLE_FROM_TISSUE` | Sample → Tissue | — |
| `SAMPLE_FROM_CELLTYPE` | Sample → CellType | confidence |
| `SAMPLE_HAS_CONDITION` | Sample → Condition | — |
| `SAMPLE_IN_STUDY` | Sample → Study | role |
| `STUDY_OF_DISEASE` | Study → Disease | — |
| `ENCODES` | Gene → Protein | — |
| `TRANSLATES_TO` | Transcript → Protein | — |
| `PARTICIPATES_IN` | Gene → Pathway | role, evidence_code |
| `EXPRESSED_IN_TISSUE` | Gene → Tissue | mean_tpm, sample_count |
| `MENTIONED_IN` | any → Paper | sentence |

Source: [`micromap_mapforge/mapping/templates/transcriptomics.yaml`](../../micromap_mapforge/mapping/templates/transcriptomics.yaml)
