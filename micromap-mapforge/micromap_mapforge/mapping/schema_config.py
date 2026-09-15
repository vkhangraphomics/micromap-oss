"""Schema-config loader (Theme A1' + A2' / issue #74).

Loads a project's `schema_config.yaml` — a LinkML-flavored ontology description
consumed by the mapper, validator, and resolver registry (post-#135). A2' adds
a directory of built-in discipline templates under ``mapping/templates/`` and
extends the loader to accept either a path or a bare template name.

This module is the LOADER layer only. It does not implement CLI surface or
template content — only resolution and structural validation. The module ships:

- The package-baked default at `mapping/templates/microbiome.yaml` (the
  microbiome shape, ported faithfully from the 2026-04-27 ontology.yaml).
- ``load_schema_config(path_or_name: Path | str | None = None) -> dict`` —
  resolves to the default when None, an explicit file when given a Path or
  existing-file string, or a baked-in template when given a bare name.
- ``DEFAULT_SCHEMA_CONFIG_PATH`` — the path constant for the default.
- ``list_builtin_templates() -> list[str]`` and
  ``builtin_template_path(name) -> Path | None`` — discovery helpers for
  the templates that ship in ``mapping/templates/`` (consumed by the CLI's
  ``mapforge templates`` subcommands in A2' slice 4).

A schema_config dict has the LinkML shape:

    {
      "name": str,                      # schema identifier
      "description": str (optional),
      "version": str (optional),
      "prefixes": dict[str, str],       # CURIE prefix → URL base
      "classes": dict[str, dict]        # class name → {is_a?, subject?, object?,
                                        #                slots?, id_prefixes?,
                                        #                x_mapforge: dict?}
    }

MapForge-specific fields (`common_columns_hint`, `normalizer`, `primary_id`,
`projection_fields`, `properties`/`status`/`notes` on relationships) live
under each class's ``x_mapforge:`` sub-dict so the file is round-trippable
through any LinkML-aware tooling. See ``mapping/templates/microbiome.yaml``
for the canonical example.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from packaging.version import InvalidVersion, Version


DEFAULT_SCHEMA_CONFIG_PATH: Path = (
    Path(__file__).parent / "templates" / "microbiome.yaml"
)


# ---------------------------------------------------------------------------
# Schema version + compat check (Theme A4 / #74)
# ---------------------------------------------------------------------------


KNOWN_SCHEMA_MAJOR: int = 1
"""The schema_config major version this mapforge release officially supports.

Bumped manually when a breaking change to the schema_config contract is
introduced (same cadence as templates bumping their MAJOR). The Layer 1
test ``test_known_schema_major_is_1`` asserts this value -- bumping the
constant requires updating the test in the same commit, which forces the
bumper to acknowledge the change in code review.
"""


def validate_version(payload: dict[str, Any]) -> None:
    """Validate a schema_config's `version:` field at load time.

    Format: PEP 440 / semver MAJOR.MINOR.PATCH parsed via
    ``packaging.version.Version``. Pre-release and build-metadata
    segments (``1.0.0-rc1``, ``1.0.0+build``) are accepted by the parser
    but discouraged in user docs.

    Compat rule: ``parsed.major`` must equal ``KNOWN_SCHEMA_MAJOR``. Lower
    raises with an "upgrade your schema_config" message; higher raises with
    an "upgrade mapforge" message. Same-major MINOR/PATCH variants pass
    silently (semver same-major = compatible).

    Args:
        payload: a structurally-valid schema_config dict (the loader's
            structural checks must have already passed).

    Raises:
        SchemaConfigError: ``version`` is missing, unparseable, or has a
            mismatched MAJOR.
    """
    if "version" not in payload:
        raise SchemaConfigError(
            "schema_config: 'version' field is required "
            "(semver MAJOR.MINOR.PATCH, e.g. '1.0.0')"
        )
    raw = payload["version"]
    # Separate "absent" (handled above) from "present but empty / malformed";
    # the empty string falls through to the InvalidVersion branch with the
    # specific "not valid semver" message rather than the generic "required"
    # one. An integer like 0 also falls through cleanly via str(raw).
    try:
        parsed = Version(str(raw))
    except InvalidVersion:
        raise SchemaConfigError(
            f"schema_config: 'version' field '{raw}' is not valid semver "
            f"(MAJOR.MINOR.PATCH expected, e.g. '1.0.0')"
        ) from None
    if parsed.major < KNOWN_SCHEMA_MAJOR:
        raise SchemaConfigError(
            f"schema_config: version '{raw}' is from an older major version "
            f"than this mapforge supports (v{KNOWN_SCHEMA_MAJOR}.x). Upgrade "
            f"your schema_config to v{KNOWN_SCHEMA_MAJOR}.x, or pin to an "
            f"older mapforge release."
        )
    if parsed.major > KNOWN_SCHEMA_MAJOR:
        raise SchemaConfigError(
            f"schema_config: version '{raw}' is from a newer major version "
            f"than this mapforge knows (v{KNOWN_SCHEMA_MAJOR}.x). Upgrade "
            f"mapforge to a release that supports schema_config v{parsed.major}.x."
        )


# ---------------------------------------------------------------------------
# Built-in template discovery (Theme A2' / #74)
# ---------------------------------------------------------------------------


_TEMPLATES_DIR: Path = Path(__file__).parent / "templates"


def list_builtin_templates() -> list[str]:
    """Return the sorted list of built-in discipline template names.

    A template is any ``<name>.yaml`` under ``mapping/templates/`` (excluding
    the ``__init__.py`` marker). Returns the bare names (no extension), sorted
    for stable CLI output.
    """
    return sorted(
        p.stem for p in _TEMPLATES_DIR.glob("*.yaml") if p.is_file()
    )


def builtin_template_path(name: str) -> Path | None:
    """Resolve a bare template name to its YAML path, or None if unknown.

    Match is case-insensitive — users often type ``Microbiome`` or
    ``MICROBIOME`` by habit. Returns ``None`` rather than raising so the
    caller owns error formatting (and can list the available alternatives in
    its own message instead of catching and re-raising here).
    """
    lower = name.lower()
    for available in list_builtin_templates():
        if available.lower() == lower:
            return _TEMPLATES_DIR / f"{available}.yaml"
    return None


class SchemaConfigError(ValueError):
    """Raised when a schema_config payload is missing required structure."""


# ---------------------------------------------------------------------------
# Ontology shape (consumed by mapper + resolver registry, A1' slices 2-3)
# ---------------------------------------------------------------------------


@dataclass
class Ontology:
    """Internal shape derived from a schema_config — what mapper and
    resolver-registry consume.

    Kept as an explicit dataclass (rather than passing the schema_config
    dict around) because both consumers were originally written against
    the legacy ``ontology.yaml`` shape (``{nodes: {...}, relationships:
    {...}}``). A1' slice 2 (mapper) and slice 3 (resolver) change the
    LOADER, not the consumer shape.
    """
    nodes: dict[str, dict[str, Any]]
    relationships: dict[str, dict[str, Any]]


def load_ontology_from_schema_config(schema_config: dict[str, Any]) -> Ontology:
    """Convert a LinkML schema_config dict into the internal Ontology shape.

    Per-class dispatch:
      - Classes with ``is_a: association`` become relationships
        (with ``subject``/``object`` → ``from``/``to`` and
        ``x_mapforge.properties``/``status``/``notes`` carried through).
      - All other classes become nodes, with ``x_mapforge.primary_id`` /
        ``identifiers`` / ``name_field`` / ``common_columns_hint`` /
        ``normalizer`` / ``notes`` pulled onto the node dict.

    Classes without an ``x_mapforge:`` block load as nodes with empty hints
    — downstream consumers will then fail to find any usable structure and
    skip the label (mapper) or build an empty resolver (registry).
    """
    nodes: dict[str, dict[str, Any]] = {}
    relationships: dict[str, dict[str, Any]] = {}
    for class_name, cls in (schema_config.get("classes") or {}).items():
        x = cls.get("x_mapforge") or {}
        if cls.get("is_a") == "association":
            relationships[class_name] = {
                "from": cls.get("subject", "any"),
                "to": cls.get("object", "any"),
                "properties": x.get("properties", []),
                "status": x.get("status", "populated"),
                "notes": x.get("notes"),
            }
        else:
            nodes[class_name] = {
                "primary_id": x.get("primary_id"),
                "identifiers": x.get("identifiers", []),
                "name_field": x.get("name_field"),
                "common_columns_hint": x.get("common_columns_hint", []),
                "normalizer": x.get("normalizer"),
                "resolver": x.get("resolver"),   # NEW (#202): {strategy, threshold?} or None
                "notes": x.get("notes"),
            }
    return Ontology(nodes=nodes, relationships=relationships)


def load_schema_config(path_or_name: Path | str | None = None) -> dict[str, Any]:
    """Load a project schema_config (LinkML + x_mapforge extensions).

    Resolution order when ``path_or_name`` is a string or Path:
      1. If the argument is an existing file → load that file.
      2. Else if the argument matches a built-in template name
         (case-insensitive, see ``list_builtin_templates()``) → load the
         baked-in template from ``mapping/templates/<name>.yaml``.
      3. Else → ``SchemaConfigError`` listing the available built-ins.

    Args:
        path_or_name: a file path (``Path`` or string) OR a bare built-in
            template name (e.g., ``"genomics"``). When ``None`` (the
            default), loads the package-baked default at
            ``DEFAULT_SCHEMA_CONFIG_PATH`` (which today resolves to the
            microbiome template).

    Returns:
        The parsed dict. Validated for the small set of structural
        requirements MapForge consumers depend on:

          - top-level ``name`` (str) is required
          - top-level ``classes`` (dict) is required and non-empty

    Raises:
        FileNotFoundError: ``path_or_name`` is a ``Path`` that doesn't
            exist (raised by the underlying read). Path arguments deliberately
            skip the bare-name fallback so a missing file fails loudly rather
            than masquerading as a typo lookup.
        SchemaConfigError: string argument matches neither an existing file
            nor a built-in template name; or the loaded payload is missing
            required ``name`` / ``classes`` structure.
    """
    resolved: Path
    if path_or_name is None:
        resolved = DEFAULT_SCHEMA_CONFIG_PATH
    elif isinstance(path_or_name, Path):
        # Path argument — never falls back to built-in name lookup. If a user
        # passes a Path that doesn't exist, that's a real "file not found",
        # not a name typo. Keeps the contract debuggable.
        resolved = path_or_name
    else:
        # String argument — try as path first, then as built-in name.
        as_path = Path(path_or_name)
        if as_path.exists() and as_path.is_file():
            resolved = as_path
        else:
            builtin = builtin_template_path(path_or_name)
            if builtin is not None:
                resolved = builtin
            else:
                # list_builtin_templates() ran inside builtin_template_path
                # already; the second call here trades a cheap re-scan for
                # readable error-message construction. Acceptable at this
                # scale (≤ ~10 templates expected).
                available = ", ".join(list_builtin_templates())
                raise SchemaConfigError(
                    f"schema_config: '{path_or_name}' is neither an existing "
                    f"file nor a built-in template. Available built-ins: "
                    f"{available}."
                )

    text = resolved.read_text(encoding="utf-8")
    payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise SchemaConfigError(
            f"schema_config at {resolved} must be a YAML mapping at the top "
            f"level; got {type(payload).__name__}"
        )
    if "name" not in payload or not payload["name"]:
        raise SchemaConfigError(
            f"schema_config at {resolved} is missing required top-level 'name'"
        )
    if not isinstance(payload.get("classes"), dict) or not payload["classes"]:
        raise SchemaConfigError(
            f"schema_config at {resolved} is missing required non-empty "
            f"'classes' dict"
        )

    # A4 / #74: schema_config version format + major-compat check. Runs
    # after structural checks and before Bioregistry so the ordering is
    # cheap-to-expensive.
    validate_version(payload)

    # A3' / #74: Bioregistry prefix validation. Runs last so structural
    # errors surface before prefix-lookup errors. The validator is in its
    # own module so the bioregistry import is isolated.
    from .bioregistry_check import validate_prefixes_against_bioregistry
    validate_prefixes_against_bioregistry(payload)

    return payload


@lru_cache(maxsize=1)
def _cached_default() -> dict[str, Any]:
    """Cached read of the package-baked default — avoids re-parsing on every
    call when many callers ask for the default in the same process."""
    return load_schema_config(DEFAULT_SCHEMA_CONFIG_PATH)


def default_schema_config() -> dict[str, Any]:
    """Return the package-baked default schema_config (cached).

    Callers MUST treat the return value as read-only — mutations would
    affect every subsequent caller in the same process. Use ``dict(...)``
    or ``copy.deepcopy(...)`` if you need to mutate.
    """
    return _cached_default()
