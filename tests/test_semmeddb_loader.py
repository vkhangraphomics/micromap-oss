"""
Tests for SemMedDB data loader.
"""

import pytest
from unittest.mock import MagicMock
from database.ingestion.semmeddb_loader import (
    SemMedDBLoader,
)


@pytest.fixture
def mock_driver():
    """Create a mock Neo4j driver."""
    driver = MagicMock()
    return driver


@pytest.fixture
def sample_records():
    """Two sample SemMedDB predication records."""
    return [
        {
            "SUBJECT_CUI": "C0022956",
            "SUBJECT_NAME": "Lactobacillus",
            "SUBJECT_SEMTYPE": "bact",
            "PREDICATE": "TREATS",
            "OBJECT_CUI": "C0011570",
            "OBJECT_NAME": "Depression",
            "OBJECT_SEMTYPE": "dsyn",
            "PMID": "12345678",
            "SENTENCE": "Lactobacillus supplementation treats depression.",
        },
        {
            "SUBJECT_CUI": "C0014834",
            "SUBJECT_NAME": "Escherichia coli",
            "SUBJECT_SEMTYPE": "bact",
            "PREDICATE": "CAUSES",
            "OBJECT_CUI": "C0009319",
            "OBJECT_NAME": "Colitis",
            "OBJECT_SEMTYPE": "dsyn",
            "PMID": "87654321",
            "SENTENCE": "E. coli causes colitis in susceptible hosts.",
        },
    ]


class TestSemMedDBTransform:
    """Tests for SemMedDBLoader.transform()."""

    def test_transform_valid_record(self, mock_driver, sample_records):
        """Valid microbiome-disease predication is transformed correctly."""
        loader = SemMedDBLoader(
            driver=mock_driver,
            organization_id="test-org",
            file_path="dummy.csv",
        )

        result = loader.transform(sample_records[0])

        assert result is not None
        assert result["taxon_id"] == "UMLS:C0022956"
        assert result["taxon_name"] == "Lactobacillus"
        assert result["disease_name"] == "Depression"
        assert result["disease_name_normalized"] == "depression"
        assert result["predicate"] == "TREATS"
        assert result["direction"] == "depleted"
        assert result["pmid"] == "12345678"
        assert result["source"] == "SemMedDB"

        # Second record
        result2 = loader.transform(sample_records[1])
        assert result2 is not None
        assert result2["taxon_id"] == "UMLS:C0014834"
        assert result2["taxon_name"] == "Escherichia coli"
        assert result2["disease_name"] == "Colitis"
        assert result2["predicate"] == "CAUSES"
        assert result2["direction"] == "enriched"
        assert result2["pmid"] == "87654321"

    def test_transform_filters_non_microbiome(self, mock_driver):
        """Records with non-microbiome subject types are filtered out."""
        loader = SemMedDBLoader(
            driver=mock_driver,
            organization_id="test-org",
            file_path="dummy.csv",
        )

        record = {
            "SUBJECT_CUI": "C0000001",
            "SUBJECT_NAME": "Aspirin",
            "SUBJECT_SEMTYPE": "phsu",
            "PREDICATE": "TREATS",
            "OBJECT_CUI": "C0011570",
            "OBJECT_NAME": "Depression",
            "OBJECT_SEMTYPE": "dsyn",
            "PMID": "11111111",
            "SENTENCE": "Aspirin treats depression.",
        }

        result = loader.transform(record)
        assert result is None

    def test_predicate_mapping(self, mock_driver):
        """Predicates are correctly mapped to association directions."""
        loader = SemMedDBLoader(
            driver=mock_driver,
            organization_id="test-org",
            file_path="dummy.csv",
        )

        base_record = {
            "SUBJECT_CUI": "C0022956",
            "SUBJECT_NAME": "Lactobacillus",
            "SUBJECT_SEMTYPE": "bact",
            "OBJECT_CUI": "C0011570",
            "OBJECT_NAME": "Depression",
            "OBJECT_SEMTYPE": "dsyn",
            "PMID": "12345678",
            "SENTENCE": "Test sentence.",
        }

        # Test enriched predicates
        for predicate in ("CAUSES", "PREDISPOSES", "COMPLICATES"):
            record = {**base_record, "PREDICATE": predicate}
            result = loader.transform(record)
            assert result is not None
            assert result["direction"] == "enriched", f"{predicate} should map to enriched"

        # Test depleted predicates
        for predicate in ("TREATS", "PREVENTS", "INHIBITS"):
            record = {**base_record, "PREDICATE": predicate}
            result = loader.transform(record)
            assert result is not None
            assert result["direction"] == "depleted", f"{predicate} should map to depleted"

        # Test unmapped predicates default to "altered"
        for predicate in ("ASSOCIATED_WITH", "AFFECTS", "COEXISTS_WITH"):
            record = {**base_record, "PREDICATE": predicate}
            result = loader.transform(record)
            assert result is not None
            assert result["direction"] == "altered", f"{predicate} should map to altered"
