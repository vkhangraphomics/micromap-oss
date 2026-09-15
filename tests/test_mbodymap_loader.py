"""
Tests for mBodyMap data loader.
"""

import pytest
from unittest.mock import MagicMock

from database.ingestion.mbodymap_loader import MBodyMapLoader


@pytest.fixture
def mock_driver():
    """Create a mock Neo4j driver."""
    return MagicMock()


@pytest.fixture
def sample_records():
    """Sample mBodyMap records for testing."""
    return [
        {
            "taxon_name": "Lactobacillus",
            "ncbi_tax_id": "1578",
            "body_site": "Gut",
            "relative_abundance": "0.05",
            "prevalence": "0.85",
            "health_status": "healthy",
        },
        {
            "taxon_name": "Streptococcus mutans",
            "ncbi_tax_id": "1309",
            "body_site": "Oral",
            "relative_abundance": "0.12",
            "prevalence": "0.92",
            "health_status": "diseased",
        },
    ]


class TestMBodyMapTransform:
    """Tests for the transform method of MBodyMapLoader."""

    def _make_loader(self, mock_driver):
        return MBodyMapLoader(
            driver=mock_driver,
            organization_id="test-org",
            file_path="/tmp/test_mbodymap.tsv",
        )

    def test_transform_valid_record(self, mock_driver, sample_records):
        """Test transform produces correct output for a valid record."""
        loader = self._make_loader(mock_driver)

        # Test first record (Lactobacillus / Gut)
        result = loader.transform(sample_records[0])

        assert result is not None
        assert result["taxon_id"] == "NCBITaxon:1578"
        assert result["taxon_name"] == "Lactobacillus"
        assert result["body_site"] == "Gut"
        assert result["body_site_normalized"] == "gut"
        assert result["body_site_id"] == "bodysite:gut"
        assert result["relative_abundance"] == 0.05
        assert result["prevalence"] == 0.85
        assert result["source"] == "mBodyMap"
        assert result["organization_id"] == "test-org"

        # Test second record (Streptococcus mutans / Oral)
        result2 = loader.transform(sample_records[1])

        assert result2 is not None
        assert result2["taxon_id"] == "NCBITaxon:1309"
        assert result2["taxon_name"] == "Streptococcus mutans"
        assert result2["body_site"] == "Oral"
        assert result2["body_site_normalized"] == "oral"
        assert result2["body_site_id"] == "bodysite:oral"
        assert result2["relative_abundance"] == 0.12
        assert result2["prevalence"] == 0.92

    def test_transform_missing_taxon_returns_none(self, mock_driver):
        """Test that records without taxon info are skipped."""
        loader = self._make_loader(mock_driver)

        record = {
            "taxon_name": "",
            "ncbi_tax_id": "",
            "body_site": "Gut",
            "relative_abundance": "0.05",
            "prevalence": "0.85",
            "health_status": "healthy",
        }

        result = loader.transform(record)
        assert result is None

    def test_transform_missing_body_site_returns_none(self, mock_driver):
        """Test that records without body_site are skipped."""
        loader = self._make_loader(mock_driver)

        record = {
            "taxon_name": "Lactobacillus",
            "ncbi_tax_id": "1578",
            "body_site": "",
            "relative_abundance": "0.05",
            "prevalence": "0.85",
            "health_status": "healthy",
        }

        result = loader.transform(record)
        assert result is None
