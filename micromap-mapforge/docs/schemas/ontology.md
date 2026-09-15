# schema_config.yaml — the project ontology format

A `schema_config.yaml` is the LinkML-flavored ontology MapForge consumes —
the mapper, resolver registry, and validator all read it (post-A1', #74).
Every project bundle carries a `schema_config.yaml` at its root, either
copied from a built-in template (`mapforge map --schema-config <name>`)
or from a user-supplied path.

> Looking for the built-in templates? See
> [`docs/schemas/discipline-templates.md`](./discipline-templates.md).

## Top-level shape

```yaml
name: <schema identifier>           # required, e.g. "micromap-genomics"
description: <human-readable>        # optional
version: "1.0.0"                     # required (semver MAJOR.MINOR.PATCH, post-A4)
last_updated: "YYYY-MM-DD"           # optional, human-readable curation date
prefixes:                            # required if any class declares id_prefixes
  HGNC: https://www.genenames.org/data/gene-symbol-report/
  # ...
classes:                             # required, non-empty
  Gene:
    # node class
  ASSOCIATED_WITH_DISEASE:
    # relationship class (is_a: association)
```

## Node classes

```yaml
Gene:
  id_prefixes: [HGNC, NCBIGene, Ensembl]
  slots: [id, symbol, gene_id, hgnc_id, ncbi_gene_id, ensembl_gene_id]
  x_mapforge:
    primary_id: gene_id              # the merge key the resolver MERGEs on
    identifiers: [gene_id, hgnc_id, ncbi_gene_id, ensembl_gene_id, symbol]
    name_field: symbol               # the human-readable name slot
    common_columns_hint: [gene, gene_symbol, hgnc_id]   # mapper heuristics
    normalizer: null                 # optional normalizer function name
    notes: >                         # optional curation rationale
      gene_id is the prefixed form (e.g. "HGNC:5") ...
```

Required `x_mapforge` fields:
- `primary_id` — the slot used as the resolver's MERGE key.
- `identifiers` — every slot that should be projected by the resolver.
- `name_field` — the slot the mapper treats as the human-readable name.

Optional `x_mapforge` fields:
- `common_columns_hint` — column-name heuristics the mapper uses to map
  source columns to this class. Each entry should be a name that
  practitioners in the discipline actually use.
- `normalizer` — string name of a normalizer function (e.g.
  `normalize_disease_name`) applied to the `primary_id` slot before MERGE.
- `notes` — free-form rationale. Mandatory for any non-obvious decision
  (see `discipline-templates.md`'s curation guidelines).

## Relationship classes

```yaml
ASSOCIATED_WITH_DISEASE:
  is_a: association                  # the LinkML marker for "this is a relationship"
  subject: Taxon                     # source class name (or "any")
  object: Disease                    # target class name (or "any")
  x_mapforge:
    properties: [effect_size, p_value, evidence_level]
    status: populated                # "populated" | "planned"
    notes: >                         # optional
      Set by disbiome_loader and similar.
```

Required `x_mapforge` fields on relationships:
- `properties` — list of edge-property slot names. May be empty (`[]`).
- `status` — `populated` (a loader emits this edge today) or `planned`
  (declared in the schema but not yet emitted; useful for downstream tools
  that scan the schema before deciding what to query).

## Validation

The loader (`mapping/schema_config.py`) enforces:
- Top-level `name` is required and non-empty.
- Top-level `classes` is required and non-empty.
- The YAML parses as a mapping at the root.

Bioregistry prefix lookup is enforced at load time post-A3' (see
[`micromap_mapforge/mapping/bioregistry_check.py`](../../micromap_mapforge/mapping/bioregistry_check.py)
— every CURIE prefix referenced by a loaded schema_config is checked against
Bioregistry's synonym-aware registry; unknown prefixes raise
`SchemaConfigError` with a fuzzy "Did you mean …?" suggestion). LinkML
structural validation (class-hierarchy correctness, slot inheritance) remains
a future scope.

## Versioning

Every schema_config declares a `version:` field in semver format
(`MAJOR.MINOR.PATCH`). Enforced at load time by `validate_version()` in
`mapping/schema_config.py`. The validator delegates to
`packaging.version.Version` for parsing — pre-release and build-metadata
segments (`1.0.0-rc1`, `1.0.0+build`) parse cleanly but are discouraged in
user-authored schemas.

The loader compares the parsed `major` to a module-level constant
`KNOWN_SCHEMA_MAJOR` (currently `1`). Same major loads silently. Lower
major raises `SchemaConfigError` containing `Upgrade your schema_config
to v<N>.x`. Higher major raises with `Upgrade mapforge to a release that
supports schema_config v<N>.x`. Verbatim wording — useful for grep when a
user surfaces an error in support.

Optional companion field: `last_updated: "YYYY-MM-DD"` records the last
curation date for human reference. Not validated by the loader.

See [`docs/schemas/discipline-templates.md`](./discipline-templates.md)
curation guideline #5 for the MAJOR/MINOR/PATCH bump rules.

When a bundle is built, the resolved `version:` is recorded in the bundle's
`manifest.json` as `schema_config_version` — see
[`docs/bundle-anatomy.md`](../bundle-anatomy.md).

## Loader API

```python
from micromap_mapforge.mapping.schema_config import (
    DEFAULT_SCHEMA_CONFIG_PATH,         # Path to the microbiome default
    load_schema_config,                  # accepts path-or-name
    list_builtin_templates,              # ["genomics", "microbiome", "transcriptomics"]
    builtin_template_path,               # name -> Path | None
    load_ontology_from_schema_config,    # dict -> Ontology dataclass
)

cfg = load_schema_config("genomics")      # bare name
cfg = load_schema_config(Path("my.yaml")) # explicit path
cfg = load_schema_config()                # microbiome default
```

See `docs/schemas/discipline-templates.md` for the built-in discipline
templates and how to extend them.
