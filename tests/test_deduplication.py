"""
Tests for entity deduplication and the enhanced normalize_disease_name function.
"""

from database.ingestion.base_loader import normalize_disease_name, DISEASE_ABBREVIATIONS


class TestNormalizeDiseaseNameBasic:
    """Test basic normalization behavior."""

    def test_empty_string(self):
        assert normalize_disease_name("") == ""

    def test_none_input(self):
        assert normalize_disease_name(None) == ""

    def test_lowercase(self):
        assert normalize_disease_name("Obesity") == "obesity"

    def test_strip_whitespace(self):
        assert normalize_disease_name("  obesity  ") == "obesity"

    def test_collapse_whitespace(self):
        assert normalize_disease_name("type  2   diabetes") == "type 2 diabetes"

    def test_case_insensitive_match(self):
        assert normalize_disease_name("Crohn's Disease") == normalize_disease_name("crohn's disease")


class TestNormalizeDiseaseNameAbbreviations:
    """Test abbreviation expansion."""

    def test_mdd(self):
        assert normalize_disease_name("MDD") == "major depressive disorder"

    def test_ibd(self):
        assert normalize_disease_name("IBD") == "inflammatory bowel disease"

    def test_ibs(self):
        assert normalize_disease_name("IBS") == "irritable bowel syndrome"

    def test_t2d(self):
        assert normalize_disease_name("T2D") == "type 2 diabetes"

    def test_t2dm(self):
        assert normalize_disease_name("T2DM") == "type 2 diabetes"

    def test_t2d_and_t2dm_match(self):
        assert normalize_disease_name("T2D") == normalize_disease_name("T2DM")

    def test_crc(self):
        assert normalize_disease_name("CRC") == "colorectal cancer"

    def test_nafld(self):
        assert normalize_disease_name("NAFLD") == "non alcoholic fatty liver disease"

    def test_ad(self):
        assert normalize_disease_name("AD") == "alzheimers disease"

    def test_pd(self):
        assert normalize_disease_name("PD") == "parkinsons disease"

    def test_ms(self):
        assert normalize_disease_name("MS") == "multiple sclerosis"

    def test_ra(self):
        assert normalize_disease_name("RA") == "rheumatoid arthritis"

    def test_asd(self):
        assert normalize_disease_name("ASD") == "autism spectrum disorder"

    def test_uc(self):
        assert normalize_disease_name("UC") == "ulcerative colitis"

    def test_cd(self):
        assert normalize_disease_name("CD") == "crohns disease"

    def test_abbreviation_case_insensitive(self):
        assert normalize_disease_name("ibd") == normalize_disease_name("IBD")

    def test_abbreviation_with_whitespace(self):
        assert normalize_disease_name("  IBD  ") == "inflammatory bowel disease"


class TestNormalizeDiseaseNamePunctuation:
    """Test punctuation handling."""

    def test_apostrophe_removal(self):
        # "Crohn's" -> "crohns"
        result = normalize_disease_name("Crohn's Disease")
        assert "'" not in result
        assert result == "crohns disease"

    def test_crohns_variants_match(self):
        assert normalize_disease_name("Crohn's Disease") == normalize_disease_name("Crohns Disease")

    def test_alzheimers_variants_match(self):
        assert normalize_disease_name("Alzheimer's Disease") == normalize_disease_name("Alzheimers Disease")

    def test_parkinsons_variants_match(self):
        assert normalize_disease_name("Parkinson's Disease") == normalize_disease_name("Parkinsons Disease")

    def test_unicode_apostrophe(self):
        # Right single quotation mark (U+2019)
        assert normalize_disease_name("Crohn\u2019s Disease") == normalize_disease_name("Crohn's Disease")

    def test_hyphen_normalization(self):
        result = normalize_disease_name("Non-Alcoholic Fatty Liver Disease")
        assert "-" not in result
        assert result == "non alcoholic fatty liver disease"

    def test_nafld_matches_full_name(self):
        abbrev = normalize_disease_name("NAFLD")
        full = normalize_disease_name("Non-Alcoholic Fatty Liver Disease")
        assert abbrev == full


class TestNormalizeDiseaseNameCrossSourceConsistency:
    """Test that different representations from various data sources normalize to the same value."""

    def test_ibd_variants(self):
        variants = [
            "Inflammatory Bowel Disease",
            "inflammatory bowel disease",
            "INFLAMMATORY BOWEL DISEASE",
            "IBD",
            "ibd",
        ]
        normalized = {normalize_disease_name(v) for v in variants}
        assert len(normalized) == 1

    def test_crohns_variants(self):
        variants = [
            "Crohn's Disease",
            "Crohns Disease",
            "crohn's disease",
            "Crohn\u2019s Disease",
            "CD",
        ]
        normalized = {normalize_disease_name(v) for v in variants}
        assert len(normalized) == 1

    def test_type2_diabetes_variants(self):
        variants = [
            "Type 2 Diabetes",
            "type 2 diabetes",
            "T2D",
            "T2DM",
        ]
        normalized = {normalize_disease_name(v) for v in variants}
        assert len(normalized) == 1

    def test_ulcerative_colitis_variants(self):
        variants = [
            "Ulcerative Colitis",
            "ulcerative colitis",
            "UC",
        ]
        normalized = {normalize_disease_name(v) for v in variants}
        assert len(normalized) == 1


class TestDiseaseAbbreviationsDict:
    """Test that the abbreviation dictionary is complete and well-formed."""

    def test_all_abbreviations_lowercase_keys(self):
        for key in DISEASE_ABBREVIATIONS:
            assert key == key.lower(), f"Key '{key}' should be lowercase"

    def test_all_abbreviations_lowercase_values(self):
        for key, value in DISEASE_ABBREVIATIONS.items():
            assert value == value.lower(), f"Value for '{key}' should be lowercase"

    def test_expected_abbreviations_present(self):
        expected = ["mdd", "ibd", "ibs", "t2d", "t2dm", "crc", "nafld",
                     "ad", "pd", "ms", "ra", "asd", "uc", "cd"]
        for abbrev in expected:
            assert abbrev in DISEASE_ABBREVIATIONS, f"Missing abbreviation: {abbrev}"
