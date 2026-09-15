"""
Tests for the PubMed/MEDLINE data loader.
"""

import pytest
from unittest.mock import MagicMock
from database.ingestion.pubmed_loader import PubMedLoader


@pytest.fixture
def mock_driver():
    """Create a mock Neo4j driver."""
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    return driver


@pytest.fixture
def sample_article():
    """Sample parsed article record matching PubMed XML structure."""
    return {
        "pmid": "35123456",
        "title": "Gut microbiota alterations in Parkinson's disease patients",
        "abstract": (
            "The gut microbiome plays a critical role in neurological health. "
            "We investigated the gut microbiota composition in Parkinson's disease "
            "patients compared to healthy controls. Lactobacillus and Bifidobacterium "
            "abundances were significantly altered."
        ),
        "journal": "Nature Microbiology",
        "year": 2022,
        "mesh_terms": [
            {"descriptor": "Parkinson Disease", "mesh_id": "D010300"},
            {"descriptor": "Gastrointestinal Microbiome", "mesh_id": "D000069196"}
        ],
        "authors": [
            {"name": "Jane Smith", "affiliation": "MIT"},
            {"name": "John Doe", "affiliation": "Harvard"}
        ]
    }


class TestPubMedLoaderTransform:
    """Tests for the PubMedLoader.transform method."""

    def test_transform_valid_article(self, mock_driver, sample_article):
        """Transform should produce a record with paper_id, source, and organization_id."""
        loader = PubMedLoader(
            driver=mock_driver,
            organization_id="test-org"
        )

        result = loader.transform(sample_article)

        assert result is not None
        assert result["paper_id"] == "PMID:35123456"
        assert result["pmid"] == "35123456"
        assert result["title"] == "Gut microbiota alterations in Parkinson's disease patients"
        assert result["journal"] == "Nature Microbiology"
        assert result["year"] == 2022
        assert result["source"] == "PubMed"
        assert result["organization_id"] == "test-org"
        assert len(result["mesh_terms"]) == 2
        assert result["mesh_terms"][0]["descriptor"] == "Parkinson Disease"
        assert result["mesh_terms"][0]["mesh_id"] == "D010300"
        assert len(result["authors"]) == 2

    def test_transform_missing_pmid_returns_none(self, mock_driver):
        """Transform should return None when pmid is missing."""
        loader = PubMedLoader(
            driver=mock_driver,
            organization_id="test-org"
        )

        result = loader.transform({"title": "Some Title"})
        assert result is None

        result = loader.transform({"pmid": "", "title": "Some Title"})
        assert result is None

        result = loader.transform({})
        assert result is None

    def test_co_mention_extraction(self, mock_driver, sample_article):
        """Transformed record should preserve the abstract for taxon text matching."""
        loader = PubMedLoader(
            driver=mock_driver,
            organization_id="test-org"
        )

        result = loader.transform(sample_article)

        assert result is not None
        assert result["abstract"] is not None
        assert len(result["abstract"]) > 0
        assert "Lactobacillus" in result["abstract"]
        assert "Bifidobacterium" in result["abstract"]
        assert "Parkinson" in result["abstract"]
