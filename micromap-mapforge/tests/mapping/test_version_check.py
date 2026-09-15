"""Unit tests for schema_config version validation (Theme A4 / #74).

These tests call validate_version() directly with minimal in-memory dicts.
Loader-integration tests live in tests/mapping/test_schema_config.py.
Template-wide regression lives in tests/mapping/test_builtin_templates.py.
"""

from __future__ import annotations

import pytest

from micromap_mapforge.mapping.schema_config import (
    KNOWN_SCHEMA_MAJOR,
    SchemaConfigError,
    validate_version,
)


def _payload(version: str | None) -> dict:
    """Minimal payload — validate_version only reads the version field."""
    if version is None:
        return {}
    return {"version": version}


# ---------------------------------------------------------------------------
# Happy path: valid semver passes
# ---------------------------------------------------------------------------


def test_valid_semver_passes():
    """Plain MAJOR.MINOR.PATCH parses cleanly."""
    for v in ("1.0.0", "1.5.0", "1.0.999"):
        validate_version(_payload(v))  # must not raise


def test_valid_semver_with_prerelease_passes():
    """PEP 440 pre-release and build-metadata segments parse fine.

    Discouraged in docs but not rejected at runtime — `packaging.version`
    is the canonical PEP 440 parser and we delegate to it.

    Note: packaging.version normalises '1.0.0-rc1' to '1.0.0rc1', and
    '1.0.0+build42' to '1.0.0+build42'. Both parse cleanly with major=1.
    '1.0.0a1' and '1.0.0.dev1' are also accepted as PEP 440 pre-releases.

    Note: packaging.version.Version('1.0') parses successfully (PEP 440
    does not require three segments) — it resolves to major=1, minor=0,
    micro=0. Similarly, Version('v1') strips the leading 'v' and parses
    as major=1. Both are valid per PEP 440 and pass validate_version()
    without adjustment. They are discouraged in user docs (three-segment
    form is canonical) but accepted at runtime.
    """
    for v in ("1.0.0-rc1", "1.0.0+build42", "1.0.0a1", "1.0.0.dev1"):
        validate_version(_payload(v))  # must not raise


# ---------------------------------------------------------------------------
# Failure path: missing or unparseable version
# ---------------------------------------------------------------------------


def test_missing_version_raises():
    """No version field at all -> SchemaConfigError mentioning 'version'."""
    with pytest.raises(SchemaConfigError) as exc_info:
        validate_version(_payload(None))
    msg = str(exc_info.value)
    assert "'version'" in msg
    assert "required" in msg


def test_invalid_format_raises():
    """Strings packaging.version can't parse all raise.

    Note: 'v1' and '1.0' are NOT in this list because packaging.version
    accepts them as valid PEP 440 versions (strips 'v', allows two-segment).
    Only strings that packaging genuinely rejects are tested here.
    """
    for v in ("draft", "latest"):
        with pytest.raises(SchemaConfigError) as exc_info:
            validate_version(_payload(v))
        msg = str(exc_info.value)
        assert v in msg, f"error message for {v!r} doesn't include the value"
        assert "not valid semver" in msg, (
            f"error message for {v!r} doesn't mention semver: {msg!r}"
        )


# ---------------------------------------------------------------------------
# Compat check: major mismatch raises with directional message
# ---------------------------------------------------------------------------


def test_older_major_raises_with_upgrade_schema_message():
    """A version below the known major says 'upgrade your schema_config'."""
    with pytest.raises(SchemaConfigError) as exc_info:
        validate_version(_payload("0.4.2"))
    msg = str(exc_info.value)
    assert "0.4.2" in msg
    assert "older major" in msg
    assert "Upgrade your schema_config" in msg


def test_newer_major_raises_with_upgrade_mapforge_message():
    """A version above the known major says 'upgrade mapforge'."""
    with pytest.raises(SchemaConfigError) as exc_info:
        validate_version(_payload("2.0.0"))
    msg = str(exc_info.value)
    assert "2.0.0" in msg
    assert "newer major" in msg
    assert "Upgrade mapforge" in msg


def test_same_major_minor_above_known_passes():
    """Same MAJOR is compatible per semver, regardless of MINOR/PATCH."""
    validate_version(_payload("1.999.999"))  # must not raise


# ---------------------------------------------------------------------------
# Deliberate-bump gate
# ---------------------------------------------------------------------------


def test_known_schema_major_is_1():
    """Pins KNOWN_SCHEMA_MAJOR == 1.

    Bumping the constant requires updating this test in the same commit --
    a code-review signal that the bump was deliberate. If you're reading
    this test because you bumped the constant: update the assertion value
    AND update the test_older_major_raises... and test_newer_major_raises...
    cases above to use versions adjacent to the new major.
    """
    assert KNOWN_SCHEMA_MAJOR == 1
