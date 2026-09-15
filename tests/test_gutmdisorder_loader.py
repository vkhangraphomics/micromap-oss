"""
Tests for the gutMDisorder data loader.
"""

import pytest
from unittest.mock import MagicMock
from database.ingestion.gutmdisorder_loader import GutMDisorderLoader
from database.ingestion.base_loader import normalize_disease_name, generate_disease_id


@pytest.fixture
def mock_driver():
    """Create a mock Neo4j driver."""
    return MagicMock()


@pytest.fixture
def sample_records():
    """Sample gutMDisorder records for testing."""
    return [
        {
            "Gut Microbe": "Lactobacillus rhamnosus",
            "NCBI ID": "568703",
            "Disorder": "Irritable Bowel Syndrome",
            "Alteration": "decreased",
            "Evidence Type": "experiment",
            "Sample Type": "stool",
            "PMID": "12345678",
        },
        {
            "Gut Microbe": "Escherichia coli",
            "NCBI ID": "562",
            "Disorder": "Crohn's Disease",
            "Alteration": "increased",
            "Evidence Type": "observation",
            "Sample Type": "biopsy",
            "PMID": "87654321",
        },
    ]


class TestGutMDisorderTransform:
    """Tests for the transform method."""

    def test_transform_valid_record(self, mock_driver, sample_records):
        """Test transforming a valid gutMDisorder record."""
        loader = GutMDisorderLoader(
            driver=mock_driver,
            organization_id="test-org",
            file_path="dummy.tsv",
        )

        result = loader.transform(sample_records[0])

        assert result is not None
        assert result["taxon_name"] == "Lactobacillus rhamnosus"
        assert result["taxon_id"] == "NCBITaxon:568703"
        assert result["ncbi_tax_id"] == "568703"
        assert result["disease_name"] == "Irritable Bowel Syndrome"
        assert result["disease_name_normalized"] == "irritable bowel syndrome"
        assert result["direction"] == "depleted"
        assert result["evidence_type"] == "experiment"
        assert result["sample_type"] == "stool"
        assert result["pmid"] == "12345678"
        assert result["source"] == "gutMDisorder"
        assert result["organization_id"] == "test-org"

    def test_transform_missing_microbe_returns_none(self, mock_driver):
        """Test that a record with missing microbe returns None."""
        loader = GutMDisorderLoader(
            driver=mock_driver,
            organization_id="test-org",
            file_path="dummy.tsv",
        )

        record = {
            "Gut Microbe": "",
            "Disorder": "IBS",
            "Alteration": "increased",
        }

        result = loader.transform(record)
        assert result is None

    def test_transform_missing_disorder_returns_none(self, mock_driver):
        """Test that a record with missing disorder returns None."""
        loader = GutMDisorderLoader(
            driver=mock_driver,
            organization_id="test-org",
            file_path="dummy.tsv",
        )

        record = {
            "Gut Microbe": "Lactobacillus",
            "Disorder": "",
            "Alteration": "increased",
        }

        result = loader.transform(record)
        assert result is None

    def test_direction_mapping(self, mock_driver):
        """Test that alteration terms are correctly mapped to directions."""
        loader = GutMDisorderLoader(
            driver=mock_driver,
            organization_id="test-org",
            file_path="dummy.tsv",
        )

        # Test enriched mappings
        for term in ["increased", "elevated", "higher", "enriched"]:
            record = {
                "Gut Microbe": "TestMicrobe",
                "Disorder": "TestDisorder",
                "Alteration": term,
            }
            result = loader.transform(record)
            assert result["direction"] == "enriched", f"Expected 'enriched' for '{term}'"

        # Test depleted mappings
        for term in ["decreased", "reduced", "lower", "depleted"]:
            record = {
                "Gut Microbe": "TestMicrobe",
                "Disorder": "TestDisorder",
                "Alteration": term,
            }
            result = loader.transform(record)
            assert result["direction"] == "depleted", f"Expected 'depleted' for '{term}'"

        # Test altered mappings
        for term in ["altered", "changed"]:
            record = {
                "Gut Microbe": "TestMicrobe",
                "Disorder": "TestDisorder",
                "Alteration": term,
            }
            result = loader.transform(record)
            assert result["direction"] == "altered", f"Expected 'altered' for '{term}'"

        # Test unknown term falls back to altered
        record = {
            "Gut Microbe": "TestMicrobe",
            "Disorder": "TestDisorder",
            "Alteration": "unknown_term",
        }
        result = loader.transform(record)
        assert result["direction"] == "altered"

    def test_disease_id_generation(self, mock_driver):
        """Test that disease IDs are generated correctly using normalized names."""
        loader = GutMDisorderLoader(
            driver=mock_driver,
            organization_id="test-org",
            file_path="dummy.tsv",
        )

        record = {
            "Gut Microbe": "Lactobacillus",
            "Disorder": "Crohn's Disease",
            "Alteration": "increased",
        }

        result = loader.transform(record)
        assert result is not None
        # Without standard identifiers, disease_id should be name-based
        expected_id = generate_disease_id("Crohn's Disease")
        assert result["disease_id"] == expected_id
        assert result["disease_name_normalized"] == normalize_disease_name("Crohn's Disease")
