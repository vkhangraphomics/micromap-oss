"""Bioregistry-backed CURIE prefix validation (Theme A3' / #74).

Hooks into ``load_schema_config()`` as the last validation step. Verifies
that every prefix referenced by a schema_config (top-level ``prefixes:``
keys and every class's ``id_prefixes:`` list) is known to Bioregistry
(via its synonym normalization) — or is one of the small set of MapForge
reserved escape-hatch names.

The Bioregistry import lives inside the function body so a missing or
broken bioregistry install fails with a clear ImportError at validation
time rather than breaking importability of the whole ``mapping/`` package.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

from .schema_config import SchemaConfigError


RESERVED_NONBIOREGISTRY_PREFIXES: frozenset[str] = frozenset({"local"})
"""MapForge-specific prefixes that bypass Bioregistry validation.

Currently just ``local`` -- the escape hatch for project-internal IDs
(e.g., ``local:patient_42_baseline``) used by the transcriptomics Sample
class. Additions require a real surfaced need; speculative additions are
declined per YAGNI.

See ``docs/schemas/discipline-templates.md`` curation guideline #4.
"""


def validate_prefixes_against_bioregistry(schema_config: dict[str, Any]) -> None:
    """Raise SchemaConfigError on the first prefix not in Bioregistry.

    Checks both the top-level ``prefixes:`` block keys and every class's
    ``id_prefixes:`` list. Each unique prefix is validated once; iteration
    order is top-level prefixes first (declaration order), then classes
    (declaration order), then each class's id_prefixes (declaration order).

    Args:
        schema_config: a structurally-valid schema_config dict (the loader's
            structural checks must have already passed).

    Raises:
        SchemaConfigError: the first prefix that is neither in
            ``RESERVED_NONBIOREGISTRY_PREFIXES`` nor recognized by
            ``bioregistry.normalize_prefix()`` (with synonym resolution).
            The message names the prefix, where it was found (top-level
            or which class), and a "Did you mean 'X'?" suggestion when
            Bioregistry has a close match.
    """
    import bioregistry

    seen: set[str] = set()

    # 1. Top-level prefixes block.
    for prefix in (schema_config.get("prefixes") or {}).keys():
        if prefix in seen:
            continue
        seen.add(prefix)
        if _is_valid_prefix(prefix, bioregistry):
            continue
        suggestion = _suggest_prefix(prefix, bioregistry)
        raise SchemaConfigError(_format_error(prefix, suggestion, class_name=None))

    # 2. Per-class id_prefixes.
    for class_name, cls in (schema_config.get("classes") or {}).items():
        for prefix in cls.get("id_prefixes") or []:
            if prefix in seen:
                continue
            seen.add(prefix)
            if _is_valid_prefix(prefix, bioregistry):
                continue
            suggestion = _suggest_prefix(prefix, bioregistry)
            raise SchemaConfigError(
                _format_error(prefix, suggestion, class_name=class_name)
            )


def unknown_prefixes(schema_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Non-raising counterpart of ``validate_prefixes_against_bioregistry``.

    Collects EVERY unknown CURIE prefix (rather than raising on the first) so a
    schema *proposal* can surface all conflicts at once (#77 D2). Each item:
    ``{prefix, where, suggestion}`` (``where`` = "top-level prefixes" or
    "class:<Name>"; ``suggestion`` = a close Bioregistry match or None).
    """
    import bioregistry

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _check(prefix: str, where: str) -> None:
        if prefix in seen:
            return
        seen.add(prefix)
        if _is_valid_prefix(prefix, bioregistry):
            return
        out.append({
            "prefix": prefix,
            "where": where,
            "suggestion": _suggest_prefix(prefix, bioregistry),
        })

    for prefix in (schema_config.get("prefixes") or {}).keys():
        _check(prefix, "top-level prefixes")
    for class_name, cls in (schema_config.get("classes") or {}).items():
        for prefix in (cls.get("id_prefixes") or []):
            _check(prefix, f"class:{class_name}")
    return out


def _is_valid_prefix(prefix: str, bioregistry_module: ModuleType) -> bool:
    """True if the prefix is either a reserved escape hatch or known to
    Bioregistry (case-insensitive via synonym normalization)."""
    if prefix in RESERVED_NONBIOREGISTRY_PREFIXES:
        return True
    return bioregistry_module.normalize_prefix(prefix) is not None


def _suggest_prefix(prefix: str, bioregistry_module: ModuleType) -> str | None:
    """Return a canonical Bioregistry prefix close to ``prefix``, or None.

    ``bioregistry.normalize_prefix`` is already case-insensitive (MONDO,
    Mondo, mondo all resolve identically), so there's no separate
    "try lowercase" pass — if ``_is_valid_prefix`` returned False, lowercase
    will too. We jump straight to ``difflib.get_close_matches`` against the
    full canonical prefix set. Cutoff 0.7 keeps suggestions tight (lower
    produces noise on unrelated typos like ``xyzzyqwerty``).
    """
    import difflib

    all_canonical = set(bioregistry_module.read_registry().keys())
    matches = difflib.get_close_matches(
        prefix.lower(), all_canonical, n=1, cutoff=0.7,
    )
    return matches[0] if matches else None


def _format_error(
    prefix: str, suggestion: str | None, class_name: str | None,
) -> str:
    """Build the user-visible error message.

    Format:
        "schema_config: unknown CURIE prefix 'X' <where>. Not in Bioregistry[. Did you mean 'Y'?]"

    The leading ``schema_config:`` prefix mirrors the other SchemaConfigError
    messages so the CLI's error path produces a single-prefixed line
    (cli.py's slice-4 polish strips its own ``schema_config:`` when the
    exception self-prefixes).
    """
    if class_name is None:
        where = "declared in top-level prefixes"
    else:
        where = f"referenced by class '{class_name}' (in id_prefixes)"

    msg = (
        f"schema_config: unknown CURIE prefix '{prefix}' {where}. "
        f"Not in Bioregistry."
    )
    if suggestion is not None:
        msg += f" Did you mean '{suggestion}'?"
    return msg
