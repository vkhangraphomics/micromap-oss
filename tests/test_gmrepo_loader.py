"""
Tests for the GMrepo data loader.
"""

import pytest
from unittest.mock import MagicMock
from database.ingestion.gmrepo_loader import GMrepoLoader


@pytest.fixture
def mock_driver():
    """Create a mock Neo4j driver."""
    return MagicMock()


@pytest.fixture
def live_api_record():
    """A taxon record shaped like the current GMrepo datasets endpoint."""
    return {
        "id": 354339,
        "disease": "D015179",
        "taxon_rank_level": "species",
        "ncbi_taxon_id": 821,
        "samples": 760,
        "abus_mean": 7.260504847,
        "abus_median": 2.976509405,
        "abus_sd": 9.954997122,
        "dblinks": "[]",
        "scientific_name": "Phocaeicola vulgatus",
        "phenotype": "Colorectal Neoplasms",
        "mesh_id": "D015179",
    }


@pytest.fixture
def sample_api_record():
    """Sample GMrepo API record for F. prausnitzii with IBS."""
    return {
        "organism_name": "Faecalibacterium prausnitzii",
        "ncbi_taxon_id": "853",
        "phenotype": "Irritable Bowel Syndrome",
        "direction": "decreased",
        "abundance_mean": 0.045,
        "abundance_std": 0.012,
        "samples_count": 150,
    }


class TestGMrepoLoaderTransform:
    """Tests for GMrepoLoader.transform()."""

    def test_transform_valid_record(self, mock_driver, sample_api_record):
        """Test transforming a valid GMrepo record produces expected output."""
        loader = GMrepoLoader(
            driver=mock_driver,
            organization_id="test-org",
        )

        result = loader.transform(sample_api_record)

        assert result is not None
        assert result["taxon_id"] == "NCBITaxon:853"
        assert result["taxon_name"] == "Faecalibacterium prausnitzii"
        assert result["ncbi_taxon_id"] == "853"
        assert result["disease_name"] == "Irritable Bowel Syndrome"
        assert result["disease_name_normalized"] == "irritable bowel syndrome"
        assert result["direction"] == "depleted"
        assert result["abundance_mean"] == 0.045
        assert result["abundance_std"] == 0.012
        assert result["samples_count"] == 150
        assert result["source"] == "gmrepo"
        assert result["organization_id"] == "test-org"

    def test_transform_missing_taxon_returns_none(self, mock_driver):
        """Test that a record with no taxon info returns None."""
        loader = GMrepoLoader(
            driver=mock_driver,
            organization_id="test-org",
        )

        record = {
            "phenotype": "Irritable Bowel Syndrome",
            "direction": "decreased",
        }

        result = loader.transform(record)
        assert result is None

    def test_transform_missing_phenotype_returns_none(self, mock_driver):
        """Test that a record with no phenotype returns None."""
        loader = GMrepoLoader(
            driver=mock_driver,
            organization_id="test-org",
        )

        record = {
            "organism_name": "Faecalibacterium prausnitzii",
            "ncbi_taxon_id": "853",
            "direction": "decreased",
        }

        result = loader.transform(record)
        assert result is None

    def test_transform_live_api_record(self, mock_driver, live_api_record):
        """Transform a record shaped like the current GMrepo datasets endpoint."""
        loader = GMrepoLoader(driver=mock_driver, organization_id="test-org")

        result = loader.transform(live_api_record)

        assert result is not None
        assert result["taxon_id"] == "NCBITaxon:821"
        assert result["taxon_name"] == "Phocaeicola vulgatus"
        assert result["rank"] == "species"
        assert result["disease_name"] == "Colorectal Neoplasms"
        assert result["abundance_mean"] == 7.260504847
        assert result["abundance_std"] == 9.954997122
        assert result["samples_count"] == 760
        # The datasets endpoint carries no enriched/depleted call.
        assert result["direction"] == "altered"


class TestGMrepoLoaderExtract:
    """Tests for GMrepoLoader.extract() against the POST-based API shape."""

    def test_extract_uses_post_and_mesh_id(self, mock_driver):
        """extract() POSTs to the trailing-slash endpoints and keys taxa by MeSH ID."""
        loader = GMrepoLoader(driver=mock_driver, organization_id="test-org")

        phenotypes_payload = {
            "phenotypes": [
                {"disease": "D006262", "term": "Health"},
                {"disease": "D015179", "term": "Colorectal Neoplasms"},
            ]
        }
        datasets_payload = {
            "associated_species": [
                {"ncbi_taxon_id": 821, "scientific_name": "Phocaeicola vulgatus",
                 "abus_mean": 7.26, "abus_sd": 9.95, "samples": 760,
                 "taxon_rank_level": "species"},
            ],
            "associated_genus": [
                {"ncbi_taxon_id": 838, "scientific_name": "Prevotella",
                 "abus_mean": 1.1, "abus_sd": 2.2, "samples": 300,
                 "taxon_rank_level": "genus"},
            ],
        }

        calls = []

        def fake_fetch(url, method="GET", json=None, params=None, **kwargs):
            calls.append((url, method, json))
            if url.endswith("/get_all_phenotypes/"):
                return phenotypes_payload
            return datasets_payload

        loader.fetch_with_retry = fake_fetch

        records = list(loader.extract())

        # Health (D006262) is skipped; only the disease phenotype is fetched.
        assert calls[0] == (
            f"{loader.api_base_url}/get_all_phenotypes/", "POST", {},
        )
        assert calls[1] == (
            f"{loader.api_base_url}/getAssociatedMicrobiotaDatasetsByPhenotypeMeshID/",
            "POST",
            {"mesh_id": "D015179"},
        )
        assert len(calls) == 2  # Health not fetched

        # One species + one genus record, both tagged with the phenotype name.
        assert len(records) == 2
        assert {r["scientific_name"] for r in records} == {
            "Phocaeicola vulgatus", "Prevotella",
        }
        assert all(r["phenotype"] == "Colorectal Neoplasms" for r in records)
        assert all(r["mesh_id"] == "D015179" for r in records)
