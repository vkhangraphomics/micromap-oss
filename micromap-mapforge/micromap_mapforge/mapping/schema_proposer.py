"""LLM-assisted schema authoring — D2 (#77). MapForge-unique: no upstream tool
proposes a BioCypher schema_config from a dataset + domain context.

Given a SourceProfile + an optional hint and base template, the LLM drafts a
BioCypher ``schema_config.yaml`` (Biolink categories, CURIE id_prefixes,
identifier fields, relationships, rationale). We parse it, sanity-check the
structure, and surface Bioregistry prefix conflicts (non-fatal — it's a draft).

Mirrors ``mapper.propose_mapping``: same Anthropic client/model convention,
cache-eligible system blocks, and ``client=`` injection for tests.
"""
from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Any

import yaml

from .bioregistry_check import unknown_prefixes
from .schema_config import SchemaConfigError
from ..inspect.types import SourceProfile

_DEFAULT_MODEL = "claude-sonnet-4-6"

_SCHEMA_AUTHORING_PROMPT = """You are MicroMap MapForge's schema author.

Given a SourceProfile (columns, types, samples) and an optional domain hint, you
propose a BioCypher schema_config.yaml for the dataset: which Biolink categories
the data maps to, their CURIE identifier prefixes, identifier fields, and the
relationships between entities.

Emit a YAML document matching this shape exactly (no prose, no fences):

name: <short-kebab-name>
description: <one-paragraph rationale: what this dataset is and how you modeled it>
version: "1.0.0"
prefixes:
  <PREFIX>: <identifier URI base>
classes:
  <BiolinkCategory>:                      # node class, e.g. Gene / Disease / Protein
    id_prefixes: [<PREFIX>, ...]
    slots: [<field>, ...]
    x_mapforge:
      primary_id: <field>                 # the merge key
      identifiers: [<field>, ...]
      name_field: <field|null>
      common_columns_hint: [<source columns that map here>]
      notes: <why this class + how columns map>
  <PredicateName>:                         # association/relationship class
    is_a: association
    subject: <BiolinkCategory>
    object: <BiolinkCategory>
    x_mapforge:
      predicate: <biolink:predicate>
      notes: <...>

Rules:
- Use Biolink category names for node classes (Gene, Disease, Protein,
  SmallMolecule, Pathway, AnatomicalEntity, PhenotypicFeature, SequenceVariant,
  OrganismTaxon, ...).
- Use REAL Bioregistry CURIE prefixes for id_prefixes (NCBITaxon, MONDO, DOID,
  HGNC, UniProt, CHEBI, CHEMBL, HMDB, ...). Prefer canonical prefixes.
- Map only columns you can justify; put your reasoning in x_mapforge.notes.
- Decompose obviously structured columns (e.g. a `d__;p__;c__` taxonomic
  lineage) into separate slots when clearly warranted.
- Emit YAML only — no markdown fences, no commentary before or after.
"""


@dataclass
class SchemaProposal:
    schema_config: dict[str, Any]
    conflicts: list[dict[str, Any]] = field(default_factory=list)  # unknown Bioregistry prefixes
    warnings: list[str] = field(default_factory=list)

    @property
    def rationale(self) -> str:
        return str(self.schema_config.get("description", ""))


def _user_message(profile: SourceProfile, hint: str | None) -> str:
    parts = ["<source_profile>", _json.dumps(profile.to_dict(), default=str, indent=2),
             "</source_profile>"]
    if hint:
        parts += ["<domain_hint>", hint, "</domain_hint>"]
    return "\n".join(parts)


def _base_template_block(base_template: dict[str, Any] | None) -> dict[str, Any] | None:
    """A cache-eligible system block offering an existing schema_config as a
    starting point / style reference."""
    if base_template is None:
        return None
    text = yaml.safe_dump(base_template, sort_keys=False)
    return {
        "type": "text",
        "text": f"<base_template>\n{text}\n</base_template>\n"
                "Use this only as a style/shape reference; model the actual dataset.",
        "cache_control": {"type": "ephemeral"},
    }


def propose_schema(
    profile: SourceProfile,
    *,
    hint: str | None = None,
    base_template: dict[str, Any] | None = None,
    client: Any | None = None,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = 4096,
) -> SchemaProposal:
    """Draft a BioCypher schema_config for ``profile`` via an LLM call.

    ``client`` is an Anthropic client; ``None`` constructs ``anthropic.Anthropic()``
    (needs ANTHROPIC_API_KEY). ``base_template`` is an optional schema_config dict
    used as a style reference. Returns the draft + surfaced Bioregistry conflicts;
    a malformed (non-dict / no-classes) LLM response raises ``SchemaConfigError``.
    """
    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    system: list[dict[str, Any]] = [{"type": "text", "text": _SCHEMA_AUTHORING_PROMPT}]
    block = _base_template_block(base_template)
    if block is not None:
        system.append(block)

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": _user_message(profile, hint)}],
    )
    text = "".join(b.text for b in response.content if hasattr(b, "text")).strip()

    try:
        schema = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise SchemaConfigError(f"LLM schema proposal was not valid YAML: {e}") from e
    if not isinstance(schema, dict) or not schema.get("classes"):
        raise SchemaConfigError(
            "LLM schema proposal must be a YAML object with a non-empty 'classes' map"
        )

    warnings: list[str] = []
    if not schema.get("version"):
        schema["version"] = "1.0.0"  # the proposer seeds a version if omitted
        warnings.append("no version proposed; defaulted to 1.0.0")

    return SchemaProposal(
        schema_config=schema,
        conflicts=unknown_prefixes(schema),
        warnings=warnings,
    )
