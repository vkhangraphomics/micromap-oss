"""Per-template smoke tests for every built-in discipline template.

Two layers:

1. **Structural validation (parametrized over every template).** The cheap
   "every template is structurally valid" gate — typos in id_prefixes, a
   missing x_mapforge.primary_id, a relationship subject/object mismatch.
   Auto-extends when a new ``mapping/templates/<name>.yaml`` lands.

2. **Per-template mapper integration (one test per template).** Proves the
   template's ``common_columns_hint`` lists actually map real-world column
   names to the right entity class. NOT parametrized — each template's
   discipline-specific test names its own canonical columns and entities so
   a failure surfaces the exact hint list at fault. Lives here (alongside
   structural validation) so adding a template means adding one file's
   worth of tests.

Slice 2 adds genomics. Slice 3 adds transcriptomics. Slice 4 adds metabolomics. Slice 5 adds proteomics.
"""

from __future__ import annotations

import pytest

from micromap_mapforge.mapping.schema_config import (
    list_builtin_templates,
    load_ontology_from_schema_config,
    load_schema_config,
)


@pytest.fixture(params=list_builtin_templates())
def template_name(request: pytest.FixtureRequest) -> str:
    """Parametrize every test in this module over all built-in templates.

    Default function scope — the value is just a string, no shared state to
    cache, no teardown. Module scope would let mutations bleed across tests
    in this file.
    """
    return request.param


def test_template_loads_via_load_schema_config(template_name: str):
    """Round-trip the YAML through load_schema_config — same code path as
    `mapforge map --schema-config <name>`."""
    cfg = load_schema_config(template_name)
    assert cfg["name"]  # any non-empty string
    assert isinstance(cfg["classes"], dict) and cfg["classes"]


def test_template_converts_to_internal_ontology_shape(template_name: str):
    """load_ontology_from_schema_config is the mapper/resolver consumer shape
    (Theme A1' slice 2-3 contract). Every template must round-trip through it
    without exception."""
    cfg = load_schema_config(template_name)
    ontology = load_ontology_from_schema_config(cfg)
    assert ontology.nodes  # must have at least one node class
    # relationships are allowed to be empty for a minimal template, but every
    # currently-shipped template has at least one.
    assert ontology.relationships


def test_template_every_node_class_declares_id_prefixes(template_name: str):
    """Every node class must declare id_prefixes — otherwise the mapper has
    nothing to anchor on and the resolver can't construct a merge key.
    Relationships (is_a: association) skip this requirement.

    Today's templates always declare prefixes; if a future template legitimately
    needs a synthetic node class with no CURIE prefix (a project-internal label
    with primary_id derived from a free-text slot), this test will need a
    matching opt-out check. That hypothetical case doesn't exist yet.
    """
    cfg = load_schema_config(template_name)
    for class_name, cls in cfg["classes"].items():
        if cls.get("is_a") == "association":
            continue
        assert cls.get("id_prefixes"), (
            f"{template_name}: node class '{class_name}' missing id_prefixes"
        )


def test_template_every_relationship_has_subject_and_object(template_name: str):
    cfg = load_schema_config(template_name)
    for class_name, cls in cfg["classes"].items():
        if cls.get("is_a") != "association":
            continue
        assert cls.get("subject"), (
            f"{template_name}: relationship '{class_name}' missing subject"
        )
        assert cls.get("object"), (
            f"{template_name}: relationship '{class_name}' missing object"
        )


def test_template_prefixes_cover_id_prefixes(template_name: str):
    """Every prefix referenced by a class's id_prefixes must be declared in
    the top-level prefixes block. Bioregistry validation in A3' depends on
    this consistency; we enforce it here as a structural smoke check.
    """
    cfg = load_schema_config(template_name)
    declared = set(cfg.get("prefixes", {}).keys())
    used: set[str] = set()
    for cls in cfg["classes"].values():
        used.update(cls.get("id_prefixes") or [])
    missing = used - declared
    # Microbiome-only gap: the microbiome template references NCBIGene from
    # Gene's id_prefixes without declaring it in top-level prefixes (the
    # ported ontology.yaml had this gap). Genomics declares NCBIGene, so the
    # allowance doesn't apply there. Bioregistry validation in A3' will tighten
    # this further; for now the per-template allowance keeps the test honest.
    known_gaps_by_template = {"microbiome": {"NCBIGene"}}
    known_gaps = known_gaps_by_template.get(template_name, set())
    assert missing <= known_gaps, (
        f"{template_name}: id_prefixes referenced but not declared in "
        f"top-level prefixes: {sorted(missing - known_gaps)}"
    )


# ---------------------------------------------------------------------------
# Per-template mapper integration — proves common_columns_hint is wired to
# real-world conventions. Each template gets its own test (parametrization
# would obscure which template failed).
# ---------------------------------------------------------------------------


def test_genomics_mapper_recognizes_canonical_columns(tmp_path):
    """A 5-row CSV with conventional genomics column names produces a heuristic
    mapping that picks up Gene, Variant, Disease, Phenotype, Paper entities.
    If any column fails to map, the genomics template's common_columns_hint
    lists are wrong."""
    from pathlib import Path
    import shutil
    from micromap_mapforge.inspect.dispatch import inspect
    from micromap_mapforge.mapping.mapper import draft_heuristic_mapping

    # Copy fixture into tmp_path so the inspect call sees a path it owns.
    fixture = Path(__file__).parent.parent / "fixtures" / "genomics_5row.csv"
    csv_path = tmp_path / "data.csv"
    shutil.copy(fixture, csv_path)

    profile = inspect(str(csv_path))
    cfg = load_schema_config("genomics")
    mapping = draft_heuristic_mapping(profile, schema_config=cfg)

    labels_in_mapping = {entity["label"] for entity in mapping.get("entities", [])}
    # Every canonical column should produce an entity:
    #   gene_symbol  -> Gene
    #   clinvar_id   -> Variant
    #   mondo_id     -> Disease
    #   hpo_id       -> Phenotype
    #   pmid         -> Paper
    assert "Gene" in labels_in_mapping, (
        f"gene_symbol column did not map to Gene; got {labels_in_mapping}"
    )
    assert "Variant" in labels_in_mapping, (
        f"clinvar_id column did not map to Variant; got {labels_in_mapping}"
    )
    assert "Disease" in labels_in_mapping, (
        f"mondo_id column did not map to Disease; got {labels_in_mapping}"
    )
    assert "Phenotype" in labels_in_mapping, (
        f"hpo_id column did not map to Phenotype; got {labels_in_mapping}"
    )
    assert "Paper" in labels_in_mapping, (
        f"pmid column did not map to Paper; got {labels_in_mapping}"
    )


# ---------------------------------------------------------------------------
# Bioregistry validation regression -- auto-extends to every template (A3' / #74)
# ---------------------------------------------------------------------------


def test_every_builtin_template_passes_bioregistry_validation(template_name: str):
    """Every shipped template must pass the Bioregistry prefix check.

    This is the gate that catches curation drift -- if a contributor adds a
    new prefix to a template without checking Bioregistry, the load fails
    here. Adding a new template under mapping/templates/ auto-extends this
    test via the parametrized template_name fixture.

    load_schema_config() runs the validator internally as its last step;
    we also call validate_prefixes_against_bioregistry() explicitly so
    this test stays a meaningful gate if the loader hook is ever removed
    or moved (regression-protective). The double-call is fast — Bioregistry
    caches its registry in memory after the first load.

    Theme A3' / #74.
    """
    from micromap_mapforge.mapping.bioregistry_check import (
        validate_prefixes_against_bioregistry,
    )
    cfg = load_schema_config(template_name)  # validates via the loader hook
    validate_prefixes_against_bioregistry(cfg)  # protects against hook removal


def test_metabolomics_mapper_recognizes_canonical_columns(tmp_path):
    """A 5-row CSV with conventional metabolomics column names produces a
    heuristic mapping that picks up Compound, Disease, Pathway, BodySite
    entities. If any column fails to map, the metabolomics template's
    common_columns_hint lists are wrong."""
    from pathlib import Path
    import shutil
    from micromap_mapforge.inspect.dispatch import inspect
    from micromap_mapforge.mapping.mapper import draft_heuristic_mapping

    fixture = Path(__file__).parent.parent / "fixtures" / "metabolomics_5row.csv"
    csv_path = tmp_path / "data.csv"
    shutil.copy(fixture, csv_path)

    profile = inspect(str(csv_path))
    cfg = load_schema_config("metabolomics")
    mapping = draft_heuristic_mapping(profile, schema_config=cfg)

    labels_in_mapping = {entity["label"] for entity in mapping.get("entities", [])}
    assert "Compound" in labels_in_mapping, (
        f"inchi_key column did not map to Compound; got {labels_in_mapping}"
    )
    assert "Disease" in labels_in_mapping, (
        f"mondo_id column did not map to Disease; got {labels_in_mapping}"
    )
    assert "Pathway" in labels_in_mapping, (
        f"kegg_pathway_id column did not map to Pathway; got {labels_in_mapping}"
    )
    assert "BodySite" in labels_in_mapping, (
        f"uberon_id column did not map to BodySite; got {labels_in_mapping}"
    )


def test_transcriptomics_mapper_recognizes_canonical_columns(tmp_path):
    """A 5-row CSV with conventional transcriptomics column names produces a
    heuristic mapping that picks up Gene, Sample, Tissue, Condition entities
    and the DIFFERENTIALLY_EXPRESSED_IN relationship. If any column fails to
    map, the transcriptomics template's common_columns_hint lists are wrong."""
    from pathlib import Path
    import shutil
    from micromap_mapforge.inspect.dispatch import inspect
    from micromap_mapforge.mapping.mapper import draft_heuristic_mapping

    fixture = Path(__file__).parent.parent / "fixtures" / "transcriptomics_5row.csv"
    csv_path = tmp_path / "data.csv"
    shutil.copy(fixture, csv_path)

    profile = inspect(str(csv_path))
    cfg = load_schema_config("transcriptomics")
    mapping = draft_heuristic_mapping(profile, schema_config=cfg)

    labels_in_mapping = {entity["label"] for entity in mapping.get("entities", [])}
    #   ensembl_gene_id -> Gene
    #   gsm             -> Sample
    #   uberon_id       -> Tissue
    #   efo_id          -> Condition  (or Disease — both have EFO; the column
    #                                   name 'efo_id' is ambiguous. We accept
    #                                   either, and rely on stricter mapping
    #                                   to a project-supplied schema config
    #                                   for disambiguation.)
    assert "Gene" in labels_in_mapping, (
        f"ensembl_gene_id column did not map to Gene; got {labels_in_mapping}"
    )
    assert "Sample" in labels_in_mapping, (
        f"gsm column did not map to Sample; got {labels_in_mapping}"
    )
    assert "Tissue" in labels_in_mapping, (
        f"uberon_id column did not map to Tissue; got {labels_in_mapping}"
    )
    # Accept either Condition or Disease for efo_id (documented ambiguity).
    assert "Condition" in labels_in_mapping or "Disease" in labels_in_mapping, (
        f"efo_id column mapped to neither Condition nor Disease; "
        f"got {labels_in_mapping}"
    )


def test_proteomics_mapper_recognizes_canonical_columns(tmp_path):
    """A 5-row CSV with conventional proteomics column names produces a
    heuristic mapping that picks up Protein, Gene, Disease, Pathway, and
    Tissue entities. If any column fails to map, the proteomics template's
    common_columns_hint lists are wrong."""
    from pathlib import Path
    import shutil
    from micromap_mapforge.inspect.dispatch import inspect
    from micromap_mapforge.mapping.mapper import draft_heuristic_mapping

    fixture = Path(__file__).parent.parent / "fixtures" / "proteomics_5row.csv"
    csv_path = tmp_path / "data.csv"
    shutil.copy(fixture, csv_path)

    profile = inspect(str(csv_path))
    cfg = load_schema_config("proteomics")
    mapping = draft_heuristic_mapping(profile, schema_config=cfg)

    labels_in_mapping = {entity["label"] for entity in mapping.get("entities", [])}
    #   uniprot_id       -> Protein
    #   gene_symbol      -> Gene
    #   mondo_id         -> Disease
    #   kegg_pathway_id  -> Pathway
    #   uberon_id        -> Tissue
    # NOTE: 'intensity' is a Measurement column; Measurement has no
    # common_columns_hint (IDs are synthetic), so it is intentionally absent.
    assert "Protein" in labels_in_mapping, (
        f"uniprot_id column did not map to Protein; got {labels_in_mapping}"
    )
    assert "Gene" in labels_in_mapping, (
        f"gene_symbol column did not map to Gene; got {labels_in_mapping}"
    )
    assert "Disease" in labels_in_mapping, (
        f"mondo_id column did not map to Disease; got {labels_in_mapping}"
    )
    assert "Pathway" in labels_in_mapping, (
        f"kegg_pathway_id column did not map to Pathway; got {labels_in_mapping}"
    )
    assert "Tissue" in labels_in_mapping, (
        f"uberon_id column did not map to Tissue; got {labels_in_mapping}"
    )


# ---------------------------------------------------------------------------
# Version-validation regression -- auto-extends to every template (A4 / #74)
# ---------------------------------------------------------------------------


def test_every_builtin_template_declares_supported_version(template_name: str):
    """Every shipped template must load cleanly through load_schema_config.

    `load_schema_config()` calls `validate_version()` internally — a missing
    `version:` field OR a `major != KNOWN_SCHEMA_MAJOR` raises
    `SchemaConfigError` from inside the load. So the meaningful gate here is
    that the load succeeds for every shipped template. Catches curation
    drift — a contributor bumping a template's MAJOR without coordinating
    with mapforge's `KNOWN_SCHEMA_MAJOR` constant produces a load failure
    that this parametrized test surfaces at PR time.

    Theme A4 / #74.
    """
    load_schema_config(template_name)  # must not raise
