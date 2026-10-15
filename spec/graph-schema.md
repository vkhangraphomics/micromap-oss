# MicroMap graph schema

Version 0.2 · describes the live MicroMap graph as of October 2026

MicroMap is a property graph (Neo4j). This document specifies its node labels, identifiers,
relationship types and provenance properties, so that other tools can produce data in the same
shape, read exports, or link their own graphs to it by shared identifiers. It describes the
format only. The graph's data is not part of this repository.

## Conventions

- **Identifiers are CURIE-style strings** (`PREFIX:local`), using the source authority's own ID
  wherever one exists. Each label has one primary identifier property (below). Cross-references
  to other authorities are kept as separate properties (`chebi_id`, `kegg_id`, `doid`, …).
- **Every node carries `organization_id`.** Public reference data uses `default`. Data a
  customer contributes carries that organization's ID and is scoped to it.
- **Every node and edge records where it came from**: `source` (the single source that created
  it) and/or `sources` (every source that has asserted it), plus `created_at` and `updated_at`.
- **Names**: `name` is the display name. `name_lower` / `name_normalized` are lookup keys and
  are not meant for display.
- **Retired labels**: `:Metabolite` was renamed to `:Compound`. Query `:Compound`.

## Entities

| Label | Primary identifier | Format | Key properties |
|---|---|---|---|
| `Taxon` | `taxon_id` | `NCBITaxon:<id>` | `name`, `rank`, `ncbi_tax_id`, `parent_taxon_id`, lineage (`kingdom` … `species`) |
| `Disease` | `disease_id` | `disease:<normalized name>` or `OMIM:<id>` | `name`, `aliases`, `doid`, `meddra_id`, `omim_id`, `icd10_code`, `category` |
| `Compound` | `compound_id` | `INCHIKEY:<key>`, else `PUBCHEM:<cid>` | `name`, `synonyms`, `inchi_key`, `hmdb_id`, `chebi_id`, `kegg_id`, `pubchem_cid`, `smiles`, `molecular_formula` |
| `Drug` | `drug_id` | `CHEMBL:<id>`, else `DGIDB:<id>` | `name`, `chembl_id`, `approved`, `max_phase`, `drug_type` |
| `DrugClass` | `name` | free text | `name_lower` |
| `Gene` | `symbol` | HGNC symbol | `name` |
| `Protein` | `protein_id` | `UniProt:<acc>`, else `CHEMBL:<id>` | `name`, `uniprot_id`, `gene_symbol`, `organism`, `target_type` |
| `Pathway` | `pathway_id` | `SMPDB:<id>` or `KEGG:<map>` | `name`, `category`, `microbial_pathway`, `host_pathway` |
| `Paper` | `paper_id` | `PMID:<pmid>` | `pmid`, `title`, `journal`, `year` |
| `Study` | `study_id` | `PMID:<pmid>`, else a source-prefixed ID | `name`, `pmid`, `sequencing_type` |
| `BodySite` | `body_site_id` | `UBERON:<id>` | `name`, `uberon_id` |
| `Trait` | `trait_id` | `<category>:<value>` | `category`, `value` |

## Relationships

| Type | From → To | Meaning | Edge properties |
|---|---|---|---|
| `HAS_PARENT` | Taxon → Taxon | NCBI taxonomy parent | — |
| `ASSOCIATED_WITH_DISEASE` | Taxon → Disease | Microbe–disease association reported in the literature | `direction`, `site_directions`, `n_studies`, `pmids`, `body_sites`, `body_site_ids`, `evidence_level`, `sources` |
| `PRODUCES` | Taxon → Compound | The microbe produces the compound | `evidence_level`, `source`, `notes` |
| `UTILIZES` | Taxon → Compound | The microbe consumes the compound | `mechanism`, `evidence_pmid`, `confidence`, `source` |
| `HAS_TRAIT` | Taxon → Trait | Phenotypic trait (metabolism, morphology, …) | `sources` |
| `MENTIONED_IN` | Taxon / Disease → Paper | The entity is discussed in the paper | `via`, `mesh_id` |
| `LINKED_TO_DISEASE` | Compound → Disease | Compound–disease link | `evidence_level`, `evidence_source`, `sources` |
| `PARTICIPATES_IN` | Compound → Pathway | Pathway membership | `evidence_source` |
| `INVESTIGATED_FOR` | Compound → Disease | Clinical investigation | `indication`, `phase`, `sponsor`, `source` |
| `ENGAGES` | Compound → Protein | Compound acts on a host protein | `mechanism`, `effect`, `linkage`, `evidence_pmid`, `confidence` |
| `PROCESSES` | Protein → Compound | Enzyme–substrate | `evidence_source` |
| `ENCODED_BY` | Protein → Gene | Gene product | `source` |
| `IMPLICATED_IN` | Pathway → Disease | Derived pathway–disease link | `method`, `support`, `min_support` |
| `TARGETS` | Drug → Protein | Drug target | `action_type`, `mechanism_description`, `direct_interaction` |
| `EFFECTIVE_AGAINST` | Drug → Taxon | Antimicrobial activity | `mechanism`, `source` |
| `DGIDB_INTERACTS_WITH` | Drug → Gene | Drug–gene interaction | `interaction_types`, `interaction_score`, `source_names`, `source_licenses`, `pmids` |
| `BELONGS_TO_CLASS` | Drug → DrugClass | Drug class membership | — |
| `SAMPLED_FROM` | Study → BodySite | Body site the study sampled | `source` |

### `ASSOCIATED_WITH_DISEASE.direction`

`direction` is one of `enriched`, `depleted` or `altered`. `altered` means a change was reported
without an agreed direction, including where sources disagree. `site_directions` keeps the
direction reported for each body site, as `<UBERON ID>|<direction>` strings. How directions from
different sources are reconciled is still being settled, so expect this section to change.

## Provenance

Findings written back into the graph use four node types and record who asserted what, from which
analysis:

| Label | Identifier | Key properties |
|---|---|---|
| `Experiment` | `id` | `title`, `started_at`, `actor_user`, `actor_org` |
| `Analysis` | `id` | `method`, `model`, `summary`, `confidence`, `occurred_at` |
| `Assertion` | `id` (content hash) | `predicate`, `direction`, `confidence`, `evidence_refs`, `asserted_at`, `valid_from` |
| `Decision` | `id` | `action_type`, `tool`, `rationale`, `summary`, `evidence_refs`, `actor_user`, `actor_org`, `role`, `occurred_at` |

| Type | From → To |
|---|---|
| `HAS_ANALYSIS` | Experiment → Analysis |
| `ASSERTED` | Analysis → Assertion |
| `SUBJECT` / `OBJECT` | Assertion → any entity |
| `SUPERSEDED_BY` | Assertion → Assertion |
| `ABOUT` | Decision → any entity |

Decisions are also kept in an append-only log that can be exported as a hash chain and checked
by anyone. See [decision-chain.md](decision-chain.md).
