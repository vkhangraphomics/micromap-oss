import pytest
from unittest.mock import MagicMock
from database.ingestion.metabo_lights_loader import MetaboLightsLoader


@pytest.fixture
def loader():
    l = MetaboLightsLoader.__new__(MetaboLightsLoader)
    l.organization_id = "test-org"
    l.stats = MagicMock()
    return l


def _study_record(**kwargs):
    """Minimal study record as would come from extract()."""
    base = {
        "study_id": "MTBLS1",
        "title": "Test Metabolomics Study",
        "description": "A test study",
        "organism": "Homo sapiens",
        "_samples": [
            {
                "sample_id": "MTBLS1:S1",
                "sample_name": "Sample 1",
                "body_site": "Blood",
                "measurements": [
                    {
                        "compound_id": "INCHIKEY:WQZGKKKJIJFFOK-GASJEMHNSA-N",
                        "compound_name": "Glucose",
                        "value": 5.4,
                        "unit": "mM",
                        "assay_type": "NMR",
                    }
                ],
            }
        ],
    }
    base.update(kwargs)
    return base


def test_transform_study_sets_study_type(loader):
    result = loader.transform(_study_record())
    assert result is not None
    assert result["study_type"] == "metabolomics"
    assert result["study_id"] == "MTBLS1"


def test_transform_generates_measurement_ids(loader):
    result = loader.transform(_study_record())
    assert result is not None
    sample = result["_samples"][0]
    m = sample["measurements"][0]
    assert m["measurement_id"] == "MTBLS1:MTBLS1:S1:INCHIKEY:WQZGKKKJIJFFOK-GASJEMHNSA-N"


def test_transform_returns_none_without_study_id(loader):
    result = loader.transform(_study_record(study_id=None))
    assert result is None


def test_transform_preserves_sample_body_site(loader):
    result = loader.transform(_study_record())
    assert result is not None
    assert result["_samples"][0]["body_site"] == "Blood"


def test_transform_study_with_no_samples(loader):
    result = loader.transform(_study_record(_samples=[]))
    assert result is not None
    assert result["_samples"] == []


def test_transform_measurement_value_is_float(loader):
    result = loader.transform(_study_record())
    assert result is not None
    m = result["_samples"][0]["measurements"][0]
    assert isinstance(m["value"], float)
    assert m["value"] == 5.4
