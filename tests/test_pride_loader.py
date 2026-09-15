import json
from unittest.mock import MagicMock

import pytest

from database.ingestion.pride_loader import PRIDELoader

SAMPLE_PRIDE_JSON = {
    "accession": "PXD001234",
    "title": "Proteomics of cancer cell lines",
    "samples": [
        {
            "sample_id": "sample_001",
            "organism_part": "kidney",
            "uberon_id": "UBERON:0002113",
            "proteins": [
                {"uniprot_accession": "P04637", "abundance": 1452.3},
                {"uniprot_accession": "P00533", "abundance": 892.1},
            ],
        },
        {
            "sample_id": "sample_002",
            "organism_part": "lung",
            "uberon_id": "UBERON:0002048",
            "proteins": [
                {"uniprot_accession": "P04637", "abundance": "not_a_number"},
            ],
        },
    ],
}


def _make_loader():
    """Build a PRIDELoader using the real constructor with a mock driver."""
    mock_driver = MagicMock()
    loader = PRIDELoader(
        driver=mock_driver,
        organization_id="test-org",
        database="neo4j",
    )
    loader.stats = MagicMock()
    return loader


@pytest.fixture
def loader():
    return _make_loader()


# ---------------------------------------------------------------------------
# extract() tests
# ---------------------------------------------------------------------------


def test_extract_reads_json_file(tmp_path, loader):
    json_file = tmp_path / "PXD001234.json"
    json_file.write_text(json.dumps(SAMPLE_PRIDE_JSON), encoding="utf-8")
    loader.file_path = str(json_file)

    records = list(loader.extract())

    assert len(records) == 1
    assert records[0]["accession"] == "PXD001234"


def test_extract_reads_directory(tmp_path, loader):
    for i in range(2):
        f = tmp_path / f"PXD00{i}.json"
        data = dict(SAMPLE_PRIDE_JSON, accession=f"PXD00{i}")
        f.write_text(json.dumps(data), encoding="utf-8")

    loader.file_path = ""
    loader.data_dir = str(tmp_path)

    records = list(loader.extract())

    assert len(records) == 2


# ---------------------------------------------------------------------------
# transform() tests
# ---------------------------------------------------------------------------


def test_transform_builds_measurement_rows(loader):
    result = loader.transform(SAMPLE_PRIDE_JSON)

    assert result is not None
    assert result["study_id"] == "PXD001234"
    assert result["study_type"] == "proteomics"
    measurements = result["_measurements"]
    # 2 proteins from sample_001 + 1 from sample_002 = 3 total
    assert len(measurements) == 3


def test_transform_measurement_id_format(loader):
    result = loader.transform(SAMPLE_PRIDE_JSON)

    assert result is not None
    measurements = result["_measurements"]
    assert measurements[0]["measurement_id"] == "PXD001234:sample_001:UNIPROT:P04637"


def test_transform_sample_type_discriminator(loader):
    result = loader.transform(SAMPLE_PRIDE_JSON)

    assert result is not None
    for sample in result["_samples"]:
        assert sample["sample_type"] == "proteomics"


def test_transform_abundance_non_numeric_becomes_none(loader):
    result = loader.transform(SAMPLE_PRIDE_JSON)

    assert result is not None
    measurements = result["_measurements"]
    # The "not_a_number" entry is from sample_002 / P04637 — the third measurement
    non_numeric = next(
        m for m in measurements if m["sample_id"] == "sample_002"
    )
    assert non_numeric["value"] is None


def test_transform_tissue_id_preserved(loader):
    result = loader.transform(SAMPLE_PRIDE_JSON)

    assert result is not None
    samples = result["_samples"]
    s1 = next(s for s in samples if s["sample_id"] == "sample_001")
    assert s1["tissue_id"] == "UBERON:0002113"


def test_transform_returns_none_on_missing_accession(loader):
    bad = dict(SAMPLE_PRIDE_JSON)
    bad.pop("accession")
    result = loader.transform(bad)
    assert result is None


def test_transform_returns_none_on_empty_accession(loader):
    bad = dict(SAMPLE_PRIDE_JSON, accession="")
    result = loader.transform(bad)
    assert result is None


# ---------------------------------------------------------------------------
# load_batch() tests
# ---------------------------------------------------------------------------


def test_load_batch_returns_dict(tmp_path, loader):
    # Wire a mock session that satisfies execute_cypher()
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(return_value=iter([]))
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.execute_write = MagicMock(return_value=[])
    mock_session.execute_read = MagicMock(return_value=[])

    mock_driver = MagicMock()
    mock_driver.session = MagicMock(return_value=mock_session)
    loader.driver = mock_driver
    loader.database = "neo4j"

    transformed = loader.transform(SAMPLE_PRIDE_JSON)
    assert transformed is not None

    result = loader.load_batch([transformed])

    assert isinstance(result, dict)
    assert "nodes_created" in result
    assert "relationships_created" in result
