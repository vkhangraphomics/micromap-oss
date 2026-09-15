from pathlib import Path

import pytest

from micromap_mapforge.contributor.validator import (
    ContributorValidationError,
    load_contributor,
    validate_contributor,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_valid_contributor_passes():
    c = load_contributor(FIXTURES / "valid_contributor.yaml")
    assert c["contributor"] == "partner-xyz"
    assert c["tier"] == "partner"
    assert c["sensitivity"] == "public"


def test_invalid_tier_rejected():
    with pytest.raises(ContributorValidationError):
        load_contributor(FIXTURES / "invalid_contributor.yaml")


def test_missing_contributor_rejected():
    with pytest.raises(ContributorValidationError):
        validate_contributor({"tier": "partner"})


def test_missing_tier_rejected():
    with pytest.raises(ContributorValidationError):
        validate_contributor({"contributor": "x"})


def test_sensitivity_optional():
    validate_contributor({"contributor": "x", "tier": "internal"})  # should not raise
