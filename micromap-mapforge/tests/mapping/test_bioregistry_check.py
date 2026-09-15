"""Unit tests for the Bioregistry-backed prefix validator (Theme A3' / #74).

These tests construct minimal in-memory schema_config dicts and call the
validator directly — no file I/O, no full loader path. Loader-integration
tests live in tests/mapping/test_schema_config.py and the template-wide
regression test lives in tests/mapping/test_builtin_templates.py.
"""

from __future__ import annotations

import pytest

from micromap_mapforge.mapping.bioregistry_check import (
    RESERVED_NONBIOREGISTRY_PREFIXES,
    validate_prefixes_against_bioregistry,
)
from micromap_mapforge.mapping.schema_config import SchemaConfigError


def _schema(prefixes: dict[str, str], classes: dict | None = None) -> dict:
    """Minimal schema_config dict for testing.

    Bioregistry-check tests call validate_prefixes_against_bioregistry()
    directly, NOT load_schema_config(), so version validation doesn't fire
    here. The version field is included for shape consistency with what
    load_schema_config() produces.
    """
    return {
        "name": "test-schema",
        "version": "1.0.0",
        "prefixes": prefixes,
        "classes": classes or {},
    }


# ---------------------------------------------------------------------------
# Happy path: canonical and synonym prefixes pass
# ---------------------------------------------------------------------------


def test_canonical_prefix_passes():
    """Lowercase canonical Bioregistry prefix is accepted."""
    validate_prefixes_against_bioregistry(
        _schema(prefixes={"mondo": "http://purl.obolibrary.org/obo/MONDO_"})
    )  # must not raise


def test_synonym_resolves_to_canonical_case_variations():
    """Non-canonical-case forms resolve via Bioregistry's synonym table."""
    for variant in ("MONDO", "mondo", "Mondo"):
        validate_prefixes_against_bioregistry(
            _schema(prefixes={variant: "https://example/"})
        )


# ---------------------------------------------------------------------------
# Failure path: unknown prefixes raise with suggestion when available
# ---------------------------------------------------------------------------


def test_unknown_prefix_raises_with_suggestion():
    """A typo close to a known prefix gets a 'Did you mean' clause."""
    with pytest.raises(SchemaConfigError) as exc_info:
        validate_prefixes_against_bioregistry(
            _schema(prefixes={"mondoo": "https://example/"})
        )
    msg = str(exc_info.value)
    assert "mondoo" in msg
    assert "Not in Bioregistry" in msg
    assert "Did you mean 'mondo'" in msg


def test_unknown_prefix_no_close_match_raises_without_suggestion():
    """A prefix with no close match in Bioregistry omits the suggestion clause."""
    with pytest.raises(SchemaConfigError) as exc_info:
        validate_prefixes_against_bioregistry(
            _schema(prefixes={"xyzzyqwerty": "https://example/"})
        )
    msg = str(exc_info.value)
    assert "xyzzyqwerty" in msg
    assert "Not in Bioregistry" in msg
    assert "Did you mean" not in msg


# ---------------------------------------------------------------------------
# Escape hatch
# ---------------------------------------------------------------------------


def test_local_escape_hatch_passes():
    """The reserved `local` prefix is always accepted, even though Bioregistry
    does not know it."""
    assert "local" in RESERVED_NONBIOREGISTRY_PREFIXES
    validate_prefixes_against_bioregistry(
        _schema(prefixes={"local": ""})
    )  # must not raise


# ---------------------------------------------------------------------------
# id_prefixes coverage
# ---------------------------------------------------------------------------


def test_id_prefixes_referenced_but_not_in_top_level_still_validated():
    """A prefix that appears only in a class's id_prefixes (not in the
    top-level prefixes block) is still validated. Pins the contract."""
    schema = _schema(
        prefixes={"hgnc": "https://www.genenames.org/"},
        classes={
            "Gene": {
                "id_prefixes": ["hgnc", "ncbigene"],
                "slots": ["id", "symbol"],
            }
        },
    )
    validate_prefixes_against_bioregistry(schema)  # ncbigene is canonical -> no raise


def test_unknown_prefix_in_id_prefixes_names_the_class():
    """An unknown prefix in id_prefixes produces an error that names the
    offending class so a user can grep for it."""
    schema = _schema(
        prefixes={"mondo": "http://purl.obolibrary.org/obo/MONDO_"},
        classes={
            "Disease": {
                "id_prefixes": ["mondo", "mondoo"],
                "slots": ["id", "name"],
            }
        },
    )
    with pytest.raises(SchemaConfigError) as exc_info:
        validate_prefixes_against_bioregistry(schema)
    msg = str(exc_info.value)
    assert "mondoo" in msg
    assert "Disease" in msg
    assert "in id_prefixes" in msg


# ---------------------------------------------------------------------------
# Fail-fast + dedup
# ---------------------------------------------------------------------------


def test_fails_on_first_unknown_prefix():
    """When multiple unknown prefixes exist, exactly one is reported (the
    first encountered in iteration order). Keeps error messages focused."""
    schema = _schema(prefixes={
        "xyzzyqwerty": "https://example/a",   # first in dict insertion order
        "qwertyxyzzy": "https://example/b",
    })
    with pytest.raises(SchemaConfigError) as exc_info:
        validate_prefixes_against_bioregistry(schema)
    msg = str(exc_info.value)
    assert "xyzzyqwerty" in msg
    assert "qwertyxyzzy" not in msg


def test_validates_dedup():
    """The same prefix appearing in both top-level prefixes and an
    id_prefixes list is checked once (no duplicate work, no duplicate error
    if it fails)."""
    # `mondoo` appears in BOTH the top-level prefixes and a class's
    # id_prefixes. The top-level pass runs first, adds `mondoo` to `seen`,
    # and the id_prefixes encounter is skipped. We pin this by asserting
    # the error attributes to the top-level (not the class).
    schema = _schema(
        prefixes={"mondoo": "https://example/"},
        classes={
            "Disease": {
                "id_prefixes": ["mondoo"],
                "slots": ["id"],
            }
        },
    )
    with pytest.raises(SchemaConfigError) as exc_info:
        validate_prefixes_against_bioregistry(schema)
    msg = str(exc_info.value)
    # Top-level wins because it iterates first; class context is suppressed.
    assert "declared in top-level prefixes" in msg
    assert "Disease" not in msg
    assert "in id_prefixes" not in msg
