"""
Tests for the Reactome pathway data loader.
"""

import pytest
from unittest.mock import MagicMock
from database.ingestion.reactome_loader import ReactomeLoader


@pytest.fixture
def mock_driver():
    """Create a mock Neo4j driver."""
    return MagicMock()


@pytest.fixture
def loader(mock_driver):
    """Create a ReactomeLoader instance with mock driver."""
    return ReactomeLoader(
        driver=mock_driver,
        organization_id="test-org",
    )


@pytest.fixture
def sample_pathway_record():
    """Sample Reactome pathway record as returned by the API."""
    return {
        "stId": "R-HSA-71403",
        "displayName": "Citric acid cycle (TCA cycle)",
        "name": ["Citric acid cycle (TCA cycle)"],
        "species": [{"displayName": "Homo sapiens", "dbId": 48887}],
        "summation": [{"text": "The citric acid cycle is a central metabolic pathway."}],
        "_category": "Metabolism",
        "_participants": [
            {
                "crossReference": [
                    {"databaseName": "ChEBI", "identifier": "16947"},
                    {"databaseName": "ChEBI", "identifier": "30089"},
                ],
                "referenceEntity": {
                    "databaseName": "ChEBI",
                    "identifier": "16947",
                },
            },
            {
                "crossReference": [],
                "referenceEntity": {
                    "databaseName": "UniProt",
                    "identifier": "P40926",
                    "geneName": ["MDH2"],
                },
            },
        ],
    }


@pytest.fixture
def sample_pathway_no_id():
    """Sample record missing pathway ID."""
    return {
        "displayName": "Some pathway",
        "_category": "Metabolism",
        "_participants": [],
    }


@pytest.fixture
def sample_pathway_no_name():
    """Sample record missing pathway name."""
    return {
        "stId": "R-HSA-99999",
        "_category": "Metabolism",
        "_participants": [],
    }


class TestReactomeTransform:
    """Tests for the transform method."""

    def test_transform_valid_record(self, loader, sample_pathway_record):
        """Test transforming a valid Reactome pathway record."""
        result = loader.transform(sample_pathway_record)

        assert result is not None
        assert result["pathway_id"] == "REACTOME:R-HSA-71403"
        assert result["reactome_id"] == "R-HSA-71403"
        assert result["name"] == "Citric acid cycle (TCA cycle)"
        assert result["description"] == "The citric acid cycle is a central metabolic pathway."
        assert result["category"] == "Metabolism"
        assert result["species"] == "Homo sapiens"
        assert result["pathway_type"] == "metabolic"
        assert result["source"] == "reactome"
        assert result["organization_id"] == "test-org"

    def test_transform_missing_pathway_id_returns_none(self, loader, sample_pathway_no_id):
        """Test that a record with missing stId returns None."""
        result = loader.transform(sample_pathway_no_id)
        assert result is None

    def test_transform_missing_name_returns_none(self, loader, sample_pathway_no_name):
        """Test that a record with missing name returns None."""
        result = loader.transform(sample_pathway_no_name)
        assert result is None

    def test_transform_extracts_chebi_ids(self, loader, sample_pathway_record):
        """Test that ChEBI identifiers are extracted from participants."""
        result = loader.transform(sample_pathway_record)

        assert result is not None
        chebi_ids = result["_chebi_ids"]
        assert "16947" in chebi_ids
        assert "30089" in chebi_ids

    def test_transform_extracts_gene_names(self, loader, sample_pathway_record):
        """Test that gene names are extracted from UniProt participants."""
        result = loader.transform(sample_pathway_record)

        assert result is not None
        gene_names = result["_gene_names"]
        assert "MDH2" in gene_names

    def test_transform_immune_pathway_type(self, loader):
        """Test that immune category yields immune pathway_type."""
        record = {
            "stId": "R-HSA-168249",
            "displayName": "Innate Immune System",
            "species": [{"displayName": "Homo sapiens"}],
            "_category": "Immune System",
            "_participants": [],
        }
        result = loader.transform(record)

        assert result is not None
        assert result["pathway_type"] == "immune"
        assert result["category"] == "Immune System"

    def test_transform_signaling_pathway_type(self, loader):
        """Test that signal transduction category yields signaling pathway_type."""
        record = {
            "stId": "R-HSA-162582",
            "displayName": "GPCR ligand binding",
            "species": [{"displayName": "Homo sapiens"}],
            "_category": "Signal Transduction",
            "_participants": [],
        }
        result = loader.transform(record)

        assert result is not None
        assert result["pathway_type"] == "signaling"

    def test_transform_no_participants(self, loader):
        """Test transform with empty participants list."""
        record = {
            "stId": "R-HSA-12345",
            "displayName": "Test Pathway",
            "species": [{"displayName": "Homo sapiens"}],
            "_category": "Metabolism",
            "_participants": [],
        }
        result = loader.transform(record)

        assert result is not None
        assert result["_chebi_ids"] == []
        assert result["_gene_names"] == []

    def test_transform_summation_as_string(self, loader):
        """Test transform when summation contains strings instead of dicts."""
        record = {
            "stId": "R-HSA-12345",
            "displayName": "Test Pathway",
            "species": [{"displayName": "Homo sapiens"}],
            "summation": ["A plain text summary."],
            "_category": "Metabolism",
            "_participants": [],
        }
        result = loader.transform(record)

        assert result is not None
        assert result["description"] == "A plain text summary."

    def test_transform_no_species(self, loader):
        """Test transform defaults to Homo sapiens when species is absent."""
        record = {
            "stId": "R-HSA-12345",
            "displayName": "Test Pathway",
            "_category": "Metabolism",
            "_participants": [],
        }
        result = loader.transform(record)

        assert result is not None
        assert result["species"] == "Homo sapiens"


class TestReactomeHelpers:
    """Tests for helper methods."""

    def test_extract_chebi_ids_from_cross_references(self, loader):
        """Test ChEBI extraction from crossReference field."""
        participants = [
            {
                "crossReference": [
                    {"databaseName": "ChEBI", "identifier": "12345"},
                    {"databaseName": "KEGG", "identifier": "C00001"},
                ],
            },
        ]
        chebi_ids = loader._extract_chebi_ids(participants)
        assert chebi_ids == ["12345"]

    def test_extract_chebi_ids_from_reference_entity(self, loader):
        """Test ChEBI extraction from referenceEntity field."""
        participants = [
            {
                "referenceEntity": {
                    "databaseName": "ChEBI",
                    "identifier": "67890",
                },
            },
        ]
        chebi_ids = loader._extract_chebi_ids(participants)
        assert chebi_ids == ["67890"]

    def test_extract_chebi_ids_deduplicates(self, loader):
        """Test that duplicate ChEBI IDs are removed."""
        participants = [
            {
                "crossReference": [
                    {"databaseName": "ChEBI", "identifier": "11111"},
                ],
                "referenceEntity": {
                    "databaseName": "ChEBI",
                    "identifier": "11111",
                },
            },
        ]
        chebi_ids = loader._extract_chebi_ids(participants)
        assert chebi_ids == ["11111"]

    def test_extract_chebi_ids_empty_participants(self, loader):
        """Test ChEBI extraction with empty participant list."""
        assert loader._extract_chebi_ids([]) == []

    def test_extract_gene_names_from_uniprot(self, loader):
        """Test gene name extraction from UniProt reference entities."""
        participants = [
            {
                "referenceEntity": {
                    "databaseName": "UniProt",
                    "identifier": "P12345",
                    "geneName": ["TP53"],
                },
            },
        ]
        gene_names = loader._extract_gene_names(participants)
        assert gene_names == ["TP53"]

    def test_extract_gene_names_string_format(self, loader):
        """Test gene name extraction when geneName is a string."""
        participants = [
            {
                "referenceEntity": {
                    "databaseName": "UniProt",
                    "identifier": "P12345",
                    "geneName": "BRCA1",
                },
            },
        ]
        gene_names = loader._extract_gene_names(participants)
        assert gene_names == ["BRCA1"]

    def test_extract_gene_names_skips_non_uniprot(self, loader):
        """Test that non-UniProt entries are skipped."""
        participants = [
            {
                "referenceEntity": {
                    "databaseName": "ChEBI",
                    "identifier": "12345",
                    "geneName": ["NOT_A_GENE"],
                },
            },
        ]
        gene_names = loader._extract_gene_names(participants)
        assert gene_names == []

    def test_extract_protein_refs_pairs_gene_name_and_uniprot(self, loader):
        """Each UniProt participant yields a {gene_name, uniprot_id} ref."""
        participants = [
            {
                "referenceEntity": {
                    "databaseName": "UniProt",
                    "identifier": "P40926",
                    "geneName": ["MDH2"],
                },
            },
        ]
        refs = loader._extract_protein_refs(participants)
        assert refs == [{"gene_name": "MDH2", "uniprot_id": "P40926"}]

    def test_extract_protein_refs_handles_missing_gene_name(self, loader):
        """A UniProt entry without a geneName still produces a ref by uniprot_id."""
        participants = [
            {
                "referenceEntity": {
                    "databaseName": "UniProt",
                    "identifier": "P12345",
                },
            },
        ]
        refs = loader._extract_protein_refs(participants)
        assert refs == [{"gene_name": None, "uniprot_id": "P12345"}]

    def test_extract_protein_refs_dedupes_repeated_pairs(self, loader):
        """Identical (gene_name, uniprot_id) pairs are deduplicated."""
        participants = [
            {
                "referenceEntity": {
                    "databaseName": "UniProt",
                    "identifier": "P12345",
                    "geneName": ["TP53"],
                },
            },
            {
                "referenceEntity": {
                    "databaseName": "UniProt",
                    "identifier": "P12345",
                    "geneName": ["TP53"],
                },
            },
        ]
        refs = loader._extract_protein_refs(participants)
        assert refs == [{"gene_name": "TP53", "uniprot_id": "P12345"}]

    def test_transform_includes_protein_refs(self, loader, sample_pathway_record):
        """transform() now populates _protein_refs alongside _gene_names."""
        result = loader.transform(sample_pathway_record)
        assert result is not None
        assert {"gene_name": "MDH2", "uniprot_id": "P40926"} in result["_protein_refs"]

    def test_filter_relevant_pathways(self, loader):
        """Test filtering pathways by microbiome-relevant keywords."""
        pathways = [
            {"displayName": "Tryptophan catabolism", "stId": "R-HSA-1"},
            {"displayName": "DNA Replication", "stId": "R-HSA-2"},
            {"displayName": "Toll-like receptor cascades", "stId": "R-HSA-3"},
            {"displayName": "Generic transcription", "stId": "R-HSA-4"},
            {"displayName": "Fatty acid metabolism", "stId": "R-HSA-5"},
        ]
        relevant = loader._filter_relevant_pathways(pathways)
        names = [p["displayName"] for p in relevant]

        assert "Tryptophan catabolism" in names
        assert "Toll-like receptor cascades" in names
        assert "Fatty acid metabolism" in names
        assert "DNA Replication" not in names
        assert "Generic transcription" not in names


class TestReactomeSourceName:
    """Test source_name property."""

    def test_source_name(self, loader):
        """Test that source_name returns 'Reactome'."""
        assert loader.source_name == "Reactome"
