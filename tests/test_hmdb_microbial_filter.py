"""HMDB microbial-subset filter correctness (#277).

The filter admitted 1,177 compounds as short-chain fatty acids (~8 are real)
because the SCFA test was an unanchored substring match:

    if scfa_name in name_lower:   # "acetate" matches ANY ester/salt
        is_scfa = True
        is_microbial = True       # ...and this is what passed the filter

so `Fenvalerate` (a pesticide), `Codeine, acetate`, `Ethyl acetate` and
`Starch acetate` were all loaded as microbial SCFAs, while the actual core
microbial metabolites — lactate, ethanol, succinate, formate, riboflavin —
were dropped.

The fixtures below are built from the REAL hmdb_metabolites.xml, verified
2026-07-15:

  HMDB0000039 Butyric acid   iupac "butanoic acid"  synonyms include "Butyrate"
  HMDB0000190 L-Lactic acid  iupac "(2S)-2-hydroxypropanoic acid"
                             synonyms include "Lactate"
  HMDB0000108 Ethanol        (name already matches the curated name)
  HMDB0000244 Riboflavin     (name already matches the curated name)

Two facts from that inspection drive the design:

1. `Butyrate`/`Lactate` are EXACT synonyms of their HMDB entries, so synonyms
   are the correct bridge from the curated names to HMDB.
2. **HMDB has no microbial signal at all.** "Microbial" appears nowhere in the
   ontology for butyric acid, lactic acid, ethanol or riboflavin — the source
   terms are only Endogenous/Food/Plant/Drug. So `is_microbial` cannot be
   derived from HMDB; it must come from the curated producer list.
"""
import pytest

from database.ingestion.hmdb_loader import (
    CURATED_MICROBIAL_NAMES,
    SCFA_NAMES,
    match_scfa,
    is_curated_microbial,
)


class TestScfaMatchIsAnchored:
    """The core #277 bug: `in name_lower` instead of an exact match."""

    @pytest.mark.parametrize("name,chain", [
        ("Acetic acid", 2),
        ("Butyric acid", 4),
        ("Propionic acid", 3),
        ("Valeric acid", 5),
        ("Caproic acid", 6),
        ("Isobutyric acid", 4),
        ("Isovaleric acid", 5),
    ])
    def test_real_scfas_still_match(self, name, chain):
        assert match_scfa([name]) == chain

    @pytest.mark.parametrize("impostor", [
        "Fenvalerate",            # pesticide      (contained "valerate")
        "Dinoseb acetate",        # herbicide      (contained "acetate")
        "Codeine, acetate",       # opioid
        "Methadyl Acetate",       # opioid
        "Ethyl acetate",          # solvent
        "Geranyl acetate",        # fragrance
        "Benzyl acetate",         # fragrance
        "Retinol acetate",        # vitamin A
        "Starch acetate",         # polymer
        "Sodium acetate",         # salt
        "Calcium acetate",        # salt
        "Iodoacetic acid",        # toxic reagent
        "Lutein diacetate",
        "3-Indolebutyric acid",   # contained "butyric acid"
        "Allyl butyrate",
        "Methyl hexanoate",
        "Glucose butyrate",
        "thiobutyric acid",
        "Oxalacetic acid",        # contained "acetic acid"
        "Acetoacetic acid",
        "Phenyl acetate",
    ])
    def test_substring_impostors_are_rejected(self, impostor):
        """REGRESSION (#277): every one of these is live-flagged is_scfa=true.

        Each fails against the old `scfa_name in name_lower` code.
        """
        assert match_scfa([impostor]) is None, (
            f"{impostor!r} is not a short-chain fatty acid — the substring "
            f"match is what let 1,177 of these through"
        )

    def test_matches_via_iupac_name(self):
        """Nothing named 'butanoate' as its display name should be missed."""
        assert match_scfa(["Some display name", "butanoate"]) == 4

    def test_case_and_whitespace_insensitive(self):
        assert match_scfa(["  ACETIC ACID  "]) == 2

    def test_ignores_none_and_empty(self):
        assert match_scfa([None, "", "Butyric acid"]) == 4
        assert match_scfa([None, ""]) is None

    def test_conjugates_are_not_scfas_despite_scfa_synonyms(self):
        """REGRESSION (#277): HMDB synonyms are NOT identity assertions.

        Real data (hmdb_metabolites.xml, verified 2026-07-15):
          Acetylglycine    (HMDB0000532) synonyms include "Acetate", "Acetic acid"
          Hexanoylcarnitine(HMDB0000756) synonyms include "Hexanoate", "Hexanoic acid"

        HMDB lists a conjugate's acyl moiety among its synonyms. Feeding
        synonyms to match_scfa flags both as SCFAs — caught only by running the
        fixed parser over the full 217,920-metabolite corpus, where is_scfa came
        back as 9 instead of the expected 7. Hence: name/IUPAC only.
        """
        assert match_scfa(["Acetylglycine", "2-acetamidoacetic acid"]) is None
        assert match_scfa(
            ["Hexanoylcarnitine",
             "(3R)-3-(hexanoyloxy)-4-(trimethylazaniumyl)butanoate"]
        ) is None

    def test_gaba_is_not_an_scfa_despite_butanoic_acid_in_its_iupac_name(self):
        """GABA's IUPAC is '4-aminobutanoic acid' — substring, not identity."""
        assert match_scfa(["gamma-Aminobutyric acid", "4-aminobutanoic acid"]) is None

    def test_steroid_and_nucleotide_rejected(self):
        """Both are live-flagged is_scfa=true today (chain 2 and 6)."""
        steroid = ("[1-(3,3-Dimethyloxiran-2-yl)-3-[(8S,10S,11S,14R)-11-hydroxy-"
                   "4,4,8,10,14-pentamethyl-3-oxo-cyclopenta[a]phenanthren-17-yl]"
                   "butyl] acetate")
        nucleotide = ("[(2R,3S,4R,5R)-5-(2,4-Dioxopyrimidin-1-yl)-4-hydroxy-"
                      "oxolan-3-yl] (3S,4R,5S)-3,4,5,6-tetrahydroxy-2-oxohexanoate")
        assert match_scfa([steroid]) is None
        assert match_scfa([nucleotide]) is None


class TestCuratedMicrobialInclusion:
    """The other half of #277: real microbial metabolites were being dropped.

    HMDB carries no microbial flag, so the curated producer list is the signal.
    """

    def test_curated_names_come_from_produces_loader(self):
        """Single source of truth — a second hardcoded list would rot (#269/#271/#272)."""
        from database.ingestion.produces_loader import TAXON_METABOLITE_PRODUCERS

        expected = {m[0].lower() for ms in TAXON_METABOLITE_PRODUCERS.values() for m in ms}
        assert CURATED_MICROBIAL_NAMES == expected
        assert len(CURATED_MICROBIAL_NAMES) > 40

    @pytest.mark.parametrize("names,why", [
        (["Ethanol"], "name matches curated 'ethanol' directly"),
        (["Riboflavin"], "name matches curated 'riboflavin' directly"),
        (["L-Lactic acid", None, "Lactate"], "real HMDB synonym 'Lactate'"),
        (["Butyric acid", "butanoic acid", "Butyrate"], "real HMDB synonym 'Butyrate'"),
        (["Succinic acid", None, "Succinate"], "synonym 'Succinate'"),
        (["Formic acid", None, "Formate"], "synonym 'Formate'"),
        (["Trimethylamine"], "name matches curated"),
        (["Indole"], "name matches curated"),
    ])
    def test_missing_metabolites_are_now_included(self, names, why):
        """REGRESSION (#277): every one of these is ABSENT from live kgdev."""
        assert is_curated_microbial(names) is True, f"should be included: {why}"

    @pytest.mark.parametrize("names", [
        ["Fenvalerate"],
        ["Ethyl acetate"],
        ["Starch acetate"],
        ["Codeine, acetate"],
        ["(+-)-Ethyl 3-hydroxy-2-methylbutyrate"],
    ])
    def test_impostors_are_not_curated_microbial(self, names):
        assert is_curated_microbial(names) is False

    def test_gaba_included_via_its_synonym(self):
        """Real data: HMDB0000112 'gamma-Aminobutyric acid' has synonym 'GABA',
        which is how the curated name 'gaba' reaches it."""
        assert is_curated_microbial(
            ["gamma-Aminobutyric acid", "4-aminobutanoic acid", "GABA"]
        ) is True

    def test_substring_does_not_leak_into_inclusion(self):
        """`is_curated_microbial` must be exact too, or we rebuild the same bug.

        'indole' is a curated name; 'Indole-3-carbinol' is a different molecule.
        """
        assert is_curated_microbial(["Indole-3-carbinol"]) is False
        assert is_curated_microbial(["Indole"]) is True

    def test_scfa_names_and_curated_names_agree_on_the_scfas(self):
        """Sanity: the curated list's SCFAs are recognised by match_scfa."""
        for scfa in ("acetate", "butyrate", "propionate", "valerate", "caproate"):
            assert scfa in CURATED_MICROBIAL_NAMES
            assert match_scfa([scfa]) == SCFA_NAMES[scfa]
