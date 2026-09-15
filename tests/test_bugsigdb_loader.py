"""
Tests for BugSigDB data loader.
"""

import pytest
from unittest.mock import MagicMock
from database.ingestion.bugsigdb_loader import BugSigDBLoader


@pytest.fixture
def mock_driver():
    """Create a mock Neo4j driver."""
    return MagicMock()


@pytest.fixture
def sample_records():
    """
    Sample BugSigDB records with a multi-taxon signature.

    Two taxa: Faecalibacterium prausnitzii and Akkermansia muciniphila,
    both associated with IBD, direction decreased, PMID 30123456.
    """
    return [
        {
            "Condition": "Inflammatory Bowel Disease",
            "Microbe Names": "Faecalibacterium prausnitzii;Akkermansia muciniphila",
            "NCBI Taxonomy IDs": "853;239935",
            "Direction": "decreased",
            "Study": "IBD gut microbiome study",
            "PMID": "30123456",
            "Body Site": "gut",
            "Sequencing Type": "16S",
            "Sample Size": "120",
        }
    ]


class TestBugSigDBTransform:
    """Tests for the BugSigDB transform method."""

    def test_transform_expands_multi_taxon_signature(self, mock_driver, sample_records):
        """Transform should yield one dict per taxon in a multi-taxon signature."""
        loader = BugSigDBLoader(
            driver=mock_driver,
            organization_id="test-org",
            file_path="/tmp/fake.tsv",
        )

        results = list(loader.transform(sample_records[0]))

        assert len(results) == 2

        # First taxon
        assert results[0]["taxon_name"] == "Faecalibacterium prausnitzii"
        assert results[0]["taxon_id"] == "NCBITaxon:853"
        assert results[0]["ncbi_tax_id"] == "853"
        assert results[0]["disease_name"] == "Inflammatory Bowel Disease"
        assert results[0]["direction"] == "depleted"
        assert results[0]["study_pmid"] == "30123456"
        assert results[0]["source"] == "BugSigDB"
        assert results[0]["organization_id"] == "test-org"

        # Second taxon
        assert results[1]["taxon_name"] == "Akkermansia muciniphila"
        assert results[1]["taxon_id"] == "NCBITaxon:239935"
        assert results[1]["ncbi_tax_id"] == "239935"
        assert results[1]["disease_name"] == "Inflammatory Bowel Disease"
        assert results[1]["direction"] == "depleted"

    def test_transform_missing_condition_returns_empty(self, mock_driver):
        """Transform should yield nothing when Condition is missing."""
        loader = BugSigDBLoader(
            driver=mock_driver,
            organization_id="test-org",
            file_path="/tmp/fake.tsv",
        )

        record = {
            "Condition": "",
            "Microbe Names": "Faecalibacterium prausnitzii",
            "NCBI Taxonomy IDs": "853",
            "Direction": "decreased",
        }

        results = list(loader.transform(record))
        assert len(results) == 0

    def test_transform_missing_microbes_returns_empty(self, mock_driver):
        """Transform should yield nothing when Microbe Names is missing."""
        loader = BugSigDBLoader(
            driver=mock_driver,
            organization_id="test-org",
            file_path="/tmp/fake.tsv",
        )

        record = {
            "Condition": "Inflammatory Bowel Disease",
            "Microbe Names": "",
            "NCBI Taxonomy IDs": "",
            "Direction": "decreased",
        }

        results = list(loader.transform(record))
        assert len(results) == 0
