from micromap_mapforge.confidence import Confidence, strictest


def test_confidence_members():
    assert Confidence.EXTRACTED.value == "EXTRACTED"
    assert Confidence.INFERRED.value == "INFERRED"
    assert Confidence.AMBIGUOUS.value == "AMBIGUOUS"


def test_strictest_returns_worst_of_multiple():
    assert strictest([Confidence.EXTRACTED, Confidence.INFERRED]) == Confidence.INFERRED
    assert strictest([Confidence.INFERRED, Confidence.AMBIGUOUS]) == Confidence.AMBIGUOUS
    assert strictest([Confidence.EXTRACTED]) == Confidence.EXTRACTED


def test_strictest_empty_raises():
    import pytest
    with pytest.raises(ValueError):
        strictest([])
