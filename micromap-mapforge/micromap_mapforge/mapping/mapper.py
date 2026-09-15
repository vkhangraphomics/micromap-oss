"""Mapper — drafts mapping.yaml dicts from a SourceProfile.

Two paths:
- draft_heuristic_mapping: deterministic, uses ontology hints (offline-safe)
- propose_mapping: LLM-assisted (added in Task 12)

Theme A1' slice 4 (#74): ``ontology.yaml`` is retired. The mapper consumes
the project's ``schema_config`` (LinkML + ``x_mapforge:`` extensions, see
``mapping/schema_config.py``) as the sole source of truth for labels and
identifier hints. ``schema_config=None`` falls back to the package default
so existing callers keep working.

The mapper's internal ``_allowed_labels`` is now parameterized by the
schema_config (any non-association class), replacing the pre-slice-4
intersection with ``mapping.schema.json``'s hardcoded label enum. The
schema's label property is relaxed in lockstep (any non-empty string).
"""

from pathlib import Path
from typing import Any

import yaml

from ..inspect.types import SourceProfile
# A1' slice 3 (#74): Ontology + load_ontology_from_schema_config moved to
# mapping/schema_config.py so the resolver registry can also consume them
# without importing from the mapper. Re-exported here for BC with any
# external callers that imported them from this module post-slice-2.
from .schema_config import (
    Ontology,
    default_schema_config,
    load_ontology_from_schema_config,
)
from .validator import validate_mapping


def _allowed_labels(schema_config: dict[str, Any]) -> set[str]:
    """Labels eligible to emit from the heuristic.

    A1' slice 4: returns the set of project node classes (every
    schema_config class without ``is_a: association``). Pre-slice-4 this
    was intersected with ``mapping.schema.json``'s hardcoded label enum;
    that enum is now ``{"type": "string", "minLength": 1}`` (any non-empty
    string), so the intersection is effectively the schema_config itself.
    """
    return {
        label
        for label, cls in (schema_config.get("classes") or {}).items()
        if cls.get("is_a") != "association"
    }


def load_ontology() -> Ontology:
    """Backward-compat wrapper — returns Ontology derived from the default
    package-baked schema_config.

    Prefer ``load_ontology_from_schema_config(default_schema_config())``
    in new code. Kept for external callers that imported this name
    pre-slice-2.
    """
    return load_ontology_from_schema_config(default_schema_config())


def _normalize_name(s: str) -> str:
    """Strip non-alphanumeric chars and lowercase, so 'ncbi_taxid',
    'ncbi-tax-id', 'NCBI Tax ID' and 'ncbi_tax_id' all canonicalize
    to 'ncbitaxid' and match each other (GH#72)."""
    return "".join(ch for ch in s.lower() if ch.isalnum())


def draft_heuristic_mapping(
    profile: SourceProfile,
    schema_config: dict[str, Any] | None = None,
    *,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Draft a mapping via column-name heuristics against a project schema_config.

    Args:
        profile: the inspector's SourceProfile of the input data source.
        schema_config: a LinkML + x_mapforge schema_config dict (see
            ``mapping/schema_config.py``). ``None`` (the default) loads the
            package-baked microbiome default, preserving pre-A1' behavior
            for callers that haven't been updated.
        source_sha256: hex digest of the source file's bytes, sealed into
            ``mapping['source']['sha256']`` for ``mapforge submit`` to read.
            ``None`` (the default) omits the field — appropriate for tests
            and any caller that doesn't compute SHA (the CLI's ``map_cmd``
            always passes a value). Theme E5 (#78).

    Never returns EXTRACTED — every entity is INFERRED. Reviewer promotes.
    Always validates against mapping.schema.json before returning.
    """
    if schema_config is None:
        schema_config = default_schema_config()
    ontology = load_ontology_from_schema_config(schema_config)
    normalized_names = [_normalize_name(c.name) for c in profile.columns]
    allowed = _allowed_labels(schema_config)

    entities: list[dict[str, Any]] = []
    matched_columns: set[str] = set()

    for label, node_def in ontology.nodes.items():
        if label not in allowed:
            continue
        hints = list(node_def.get("common_columns_hint", []))
        columns_map: dict[str, str] = {}
        match_on_col: str | None = None

        already_used: set[str] = set(matched_columns)
        for identifier in node_def.get("identifiers", []):
            col = _find_column(normalized_names, profile, [identifier],
                               exclude=already_used)
            if col:
                columns_map[identifier] = col
                if match_on_col is None:
                    match_on_col = identifier
                matched_columns.add(col)
                already_used.add(col)
                break

        name_field = node_def.get("name_field")
        if name_field and name_field not in columns_map:
            col = _find_column(normalized_names, profile, hints,
                               exclude=already_used)
            if col:
                columns_map[name_field] = col
                matched_columns.add(col)

        if not columns_map:
            continue
        if match_on_col is None:
            match_on_col = next(iter(columns_map.keys()))

        entity: dict[str, Any] = {
            "label": label,
            "match_on": match_on_col,
            "columns": columns_map,
            "confidence": "INFERRED",
        }
        if node_def.get("normalizer"):
            entity["normalizer"] = node_def["normalizer"]
        entities.append(entity)

    source: dict[str, Any] = {
        "name": Path(profile.path).stem,
        "format": profile.format,
        "path": profile.path,
    }
    if source_sha256:
        # E5 (#78): seal the source SHA into mapping.yaml so submit can
        # populate Contribution.source_sha256 without re-opening the file.
        source["sha256"] = source_sha256
    mapping = {
        "source": source,
        "entities": entities,
        "relationships": [],
    }
    validate_mapping(mapping)
    return mapping


def _find_column(
    normalized_names: list[str],
    profile: SourceProfile,
    candidates: list[str],
    *,
    exclude: set[str] | None = None,
) -> str | None:
    """Return the original-cased source column whose normalized name
    matches (or contains) any candidate's normalized form. Columns in
    `exclude` are skipped.

    Two-pass: exact normalized match across ALL candidates first, then
    sub-token containment. Otherwise a substring hit on an early
    candidate ('tax_id' inside 'ncbi_species_taxid') would shadow a
    later candidate's perfect match ('organism' on 'ncbi_organism_name').
    """
    excluded = exclude or set()
    norm_cands = [_normalize_name(c) for c in candidates if _normalize_name(c)]

    # Pass 1: exact normalized match.
    for cand_n in norm_cands:
        if cand_n in normalized_names:
            i = normalized_names.index(cand_n)
            col = profile.columns[i].name
            if col not in excluded:
                return col

    # Pass 2: substring containment. Try longer candidates first — a more
    # specific token ('organism', 8 chars) should win over a shorter one
    # ('taxid', 5) so 'organism' picks 'ncbi_organism_name' before 'taxid'
    # accidentally claims 'ncbi_species_taxid' for a name-field search.
    for cand_n in sorted(norm_cands, key=len, reverse=True):
        for i, n in enumerate(normalized_names):
            col = profile.columns[i].name
            if col in excluded:
                continue
            if cand_n in n:
                return col
    return None


# -- LLM proposal path --

import json as _json
from typing import Any as _Any

_SYSTEM_PROMPT = """You are MicroMap MapForge's schema mapper.

You receive:
1. MicroMap's ontology (node labels, identifiers, normalizers).
2. A SourceProfile of a user-submitted data source (columns, types, samples).
3. Optional user hint describing the dataset.

You emit a YAML document matching this shape exactly (no prose, no fences):

source:
  name: <short name>
  format: <csv|tsv|json|jsonl|parquet|sql_dump>
  path: <original path>
entities:
  - label: <one of the ontology node labels>
    match_on: <identifier field name from ontology>
    columns:
      <ontology_field>: <source_column_name>
    confidence: EXTRACTED | INFERRED
    # include normalizer: <name> only if the ontology entry declares one
relationships:
  - type: <relationship type from ontology>
    from: "<Label>(<field>=row.<col>)"
    to:   "<Label>(<field>=row.<col>)"
    properties:
      <rel_prop>: <source_column_name>
      # or a constant: <rel_prop>: { constant: <value> }
    confidence: EXTRACTED | INFERRED

Rules:
- Only use labels/identifiers/relationship types present in the ontology.
- Use EXTRACTED only when a source column exactly matches a canonical identifier
  (e.g. a column named 'ncbi_tax_id' holding ints).
- Otherwise use INFERRED.
- If you cannot confidently map a column, omit it. Do not invent fields.
- Emit YAML only — no markdown fences, no commentary before or after.
"""


def _build_user_message(profile: SourceProfile, hint: str | None) -> str:
    profile_json = _json.dumps(profile.to_dict(), default=str, indent=2)
    parts = ["<source_profile>", profile_json, "</source_profile>"]
    if hint:
        parts += ["<user_hint>", hint, "</user_hint>"]
    return "\n".join(parts)


def _ontology_cache_block(schema_config: dict[str, _Any] | None = None) -> dict[str, _Any]:
    """Return the ontology as a cache-eligible system block.

    A1' slice 2: builds the block from the project ``schema_config`` dict
    (serialized as YAML) instead of dumping ``ontology.yaml``. Distinct
    projects get distinct cache keys (different content); same project
    reuses the cache across calls.
    """
    if schema_config is None:
        schema_config = default_schema_config()
    ontology_text = yaml.safe_dump(schema_config, sort_keys=False)
    return {
        "type": "text",
        "text": f"<micromap_ontology>\n{ontology_text}\n</micromap_ontology>",
        "cache_control": {"type": "ephemeral"},
    }


def propose_mapping(
    profile: SourceProfile,
    hint: str | None = None,
    client: _Any | None = None,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 2048,
    schema_config: dict[str, _Any] | None = None,
    *,
    source_sha256: str | None = None,
) -> dict[str, _Any]:
    """Propose a mapping.yaml dict via an LLM call. Validates before returning.

    `client` is an Anthropic client. If None, constructs `anthropic.Anthropic()`
    which requires ANTHROPIC_API_KEY in env.

    `schema_config` is the project's LinkML + x_mapforge schema; None falls
    back to the package-baked default (matches pre-A1' behavior).

    `source_sha256` (E5, #78) — hex digest of the source file's bytes. The
    LLM doesn't compute hashes; we patch it into ``mapping['source']['sha256']``
    server-side, after parsing the LLM's YAML and before structural validation.
    ``None`` (the default) omits the field — appropriate for tests and any
    caller that doesn't compute SHA (the CLI's ``map_cmd`` always passes a
    value).
    """
    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[
            {"type": "text", "text": _SYSTEM_PROMPT},
            _ontology_cache_block(schema_config),
        ],
        messages=[{"role": "user", "content": _build_user_message(profile, hint)}],
    )

    text = "".join(
        block.text for block in response.content if hasattr(block, "text")
    ).strip()

    try:
        mapping = yaml.safe_load(text)
    except yaml.YAMLError as e:
        from .validator import MappingValidationError
        raise MappingValidationError(f"LLM response was not valid YAML: {e}") from e

    if not isinstance(mapping, dict):
        from .validator import MappingValidationError
        raise MappingValidationError(
            f"LLM response was not a YAML object: got {type(mapping).__name__}"
        )

    # E5 (#78): seal the source SHA into the LLM-returned mapping. The LLM
    # doesn't compute hashes; we patch it in server-side before validation.
    if source_sha256 and isinstance(mapping.get("source"), dict):
        mapping["source"]["sha256"] = source_sha256

    validate_mapping(mapping)
    return mapping
