"""Curated-name -> canonical HMDB compound resolution (#276).

The curated PRODUCES data calls a molecule "Butyrate"; HMDB calls it
"Butyric acid" (IUPAC "butanoic acid"). Nothing joined them, so all 218,454
PRODUCES edges landed on identifier-less `name_lower` nodes and ZERO
taxon->compound->disease paths existed (#271).

Name/IUPAC matching alone recovers only 0.3% of those edges. Synonyms are the
bridge — "Butyrate" and "Lactate" are real exact HMDB synonyms of Butyric acid
and L-Lactic acid (verified against hmdb_metabolites.xml; parsed and persisted
by #277/PR #278).

But synonyms are NOT identity assertions. HMDB lists a conjugate's acyl moiety
among its synonyms:

    Acetylglycine     (HMDB0000532) synonyms: "Acetate", "Acetic acid"
    Hexanoylcarnitine (HMDB0000756) synonyms: "Hexanoate", "Hexanoic acid"

So `acetate` matches BOTH Acetic acid (right) and Acetylglycine (wrong). Acetate
carries 43,108 producer edges — the largest bucket in the graph — so a wrong or
arbitrary pick is far more damaging than no pick at all.

Hence: resolve by a preference ladder, demand uniqueness at the winning tier,
and refuse (returning None -> the safe legacy curated node) rather than guess.
Genuine ambiguity is recorded as an explicit curation decision in
CURATED_COMPOUND_HMDB_IDS, not inferred.
"""
from unittest.mock import MagicMock

from database.ingestion.produces_loader import (
    CURATED_COMPOUND_HMDB_IDS,
    ProducesLoader,
)


def _loader():
    inst = ProducesLoader(driver=MagicMock(), organization_id="test-org")
    inst._responses = []
    inst._calls = []

    def fake_execute(cypher, params=None, write=True):
        inst._calls.append((cypher, params or {}))
        return inst._responses.pop(0) if inst._responses else []

    inst.execute_cypher = fake_execute
    return inst


def _cand(compound_id, name, hmdb_id=None, name_match=False, iupac_match=False,
          status="quantified"):
    return {
        "compound_id": compound_id, "name": name, "hmdb_id": hmdb_id,
        "name_match": name_match, "iupac_match": iupac_match,
        "hmdb_status": status,
    }


class TestEvidenceTierBeatsNameMatch:
    """REGRESSION (#276): HMDB has predicted stubs holding the plainer name.

    Real data, verified against hmdb_metabolites.xml (2026-07-15):

      HMDB0304356 "formate"      status=expected    0 diseases,  0 pathways, no KEGG
      HMDB0000142 "Formic acid"  status=quantified 14 diseases, 39 pathways, C00058
                                 (lists "Formate" only as a SYNONYM)

    A pure name>synonym ladder picks the stub, and formate's 4,376 producer
    edges land on a node with nothing downstream — leaving the mechanistic hop
    severed, which is the entire thing #276 exists to repair. So evidence tier
    is considered before match tier.
    """

    def test_quantified_synonym_beats_expected_name_match(self):
        loader = _loader()
        loader._responses = [[
            _cand("INCHIKEY:STUB", "formate", "HMDB0304356",
                  name_match=True, status="expected"),
            _cand("INCHIKEY:REAL", "Formic acid", "HMDB0000142",
                  status="quantified"),
        ]]
        assert loader._resolve_canonical_compound("formate") == "INCHIKEY:REAL"

    def test_within_same_tier_name_match_still_wins(self):
        """Evidence tier only breaks ties across quality, not within it."""
        loader = _loader()
        loader._responses = [[
            _cand("INCHIKEY:SYN", "Acetylglycine", "HMDB0000532", status="quantified"),
            _cand("INCHIKEY:NAME", "Indole", "HMDB0000738",
                  name_match=True, status="quantified"),
        ]]
        assert loader._resolve_canonical_compound("indole") == "INCHIKEY:NAME"

    def test_a_better_evidenced_synonym_does_not_steal_a_real_name_match(self):
        """REGRESSION (#276): the vitamin K2 trap — found on LIVE data.

        Live candidates for the curated name 'vitamin k2':
            vitamin K2     HMDB0259856  detected    <- the right molecule, NAME match
            Menatetrenone  HMDB0030017  detected    <- MK-4, synonym match
            Menadione      HMDB0001892  quantified  <- vitamin K3! DIFFERENT molecule

        Taking the single best evidence tier picks Menadione — a synonym match on
        the wrong molecule — purely because it is better characterised. 'vitamin
        k2' carries 37,832 producer edges, the 2nd largest bucket in the graph.

        Evidence must only *demote stubs*, never let a better-evidenced synonym
        outrank a real (non-stub) name match.
        """
        loader = _loader()
        loader._responses = [[
            _cand("INCHIKEY:MK4", "Menatetrenone", "HMDB0030017", status="detected"),
            _cand("INCHIKEY:K2", "vitamin K2", "HMDB0259856",
                  name_match=True, status="detected"),
            _cand("INCHIKEY:K3", "Menadione", "HMDB0001892", status="quantified"),
        ]]
        assert loader._resolve_canonical_compound("vitamin k2") == "INCHIKEY:K2", (
            "must not resolve vitamin K2 to Menadione (vitamin K3)"
        )

    def test_stub_name_match_still_loses_to_a_real_synonym(self):
        """The formate case must keep working — evidence still demotes stubs."""
        loader = _loader()
        loader._responses = [[
            _cand("INCHIKEY:STUB", "formate", "HMDB0304356",
                  name_match=True, status="expected"),
            _cand("INCHIKEY:REAL", "Formic acid", "HMDB0000142", status="quantified"),
        ]]
        assert loader._resolve_canonical_compound("formate") == "INCHIKEY:REAL"

    def test_predicted_only_candidate_is_still_used(self):
        """Don't refuse a stub if it's all we have — some data beats none."""
        loader = _loader()
        loader._responses = [[
            _cand("INCHIKEY:ONLY", "Prodigiosin", "HMDB0999999",
                  name_match=True, status="predicted"),
        ]]
        assert loader._resolve_canonical_compound("prodigiosin") == "INCHIKEY:ONLY"

    def test_missing_status_is_not_preferred_over_quantified(self):
        loader = _loader()
        loader._responses = [[
            _cand("INCHIKEY:NOSTATUS", "Butanoate", "HMDB0300000",
                  name_match=True, status=None),
            _cand("INCHIKEY:REAL", "Butyric acid", "HMDB0000039", status="quantified"),
        ]]
        assert loader._resolve_canonical_compound("butanoate") == "INCHIKEY:REAL"


class TestResolutionLadder:
    def test_exact_name_match_wins(self):
        loader = _loader()
        loader._responses = [[
            _cand("INCHIKEY:ETH", "Ethanol", "HMDB0000108", name_match=True),
        ]]
        assert loader._resolve_canonical_compound("ethanol") == "INCHIKEY:ETH"

    def test_iupac_match_used_when_no_name_match(self):
        loader = _loader()
        loader._responses = [[
            _cand("INCHIKEY:BUT", "Butyric acid", "HMDB0000039", iupac_match=True),
        ]]
        assert loader._resolve_canonical_compound("butanoic acid") == "INCHIKEY:BUT"

    def test_synonym_only_match_used_when_unique(self):
        """The whole point: 'Butyrate' -> Butyric acid via synonym."""
        loader = _loader()
        loader._responses = [[
            _cand("INCHIKEY:FERIUCNNQQJTOY-UHFFFAOYSA-N", "Butyric acid", "HMDB0000039"),
        ]]
        assert loader._resolve_canonical_compound("butyrate") == (
            "INCHIKEY:FERIUCNNQQJTOY-UHFFFAOYSA-N"
        )

    def test_name_match_beats_synonym_match(self):
        """REGRESSION (#276): the Acetylglycine trap.

        'acetate' hits Acetic acid (by synonym) and Acetylglycine (by synonym).
        If one had matched by NAME it must win outright over any synonym hit.
        """
        loader = _loader()
        loader._responses = [[
            _cand("INCHIKEY:WRONG", "Acetylglycine", "HMDB0000532"),
            _cand("INCHIKEY:RIGHT", "Acetate", "HMDB0000042", name_match=True),
        ]]
        assert loader._resolve_canonical_compound("acetate") == "INCHIKEY:RIGHT"

    def test_no_candidates_returns_none(self):
        loader = _loader()
        loader._responses = [[]]
        assert loader._resolve_canonical_compound("prodigiosin") is None


class TestAmbiguityIsRefusedNotGuessed:
    """These pass explicit_ids={} to exercise the ladder itself.

    `acetate` and `lactate` are pinned in the real CURATED_COMPOUND_HMDB_IDS
    precisely BECAUSE they are ambiguous — so the default path resolves them.
    What's under test here is what happens to an ambiguous name that nobody has
    made a decision about yet, which is the case any newly-curated metabolite
    starts in.
    """

    def test_ambiguous_synonym_match_refuses(self, caplog):
        """REGRESSION (#276): acetate's 43,108 edges must not land on a guess."""
        loader = _loader()
        loader._responses = [[
            _cand("INCHIKEY:ACID", "Acetic acid", "HMDB0000042"),
            _cand("INCHIKEY:GLY", "Acetylglycine", "HMDB0000532"),
        ]]
        with caplog.at_level("WARNING"):
            assert loader._resolve_canonical_compound("acetate", explicit_ids={}) is None
        assert "ambiguous" in caplog.text.lower()
        assert "acetate" in caplog.text

    def test_ambiguity_warning_names_the_candidates_and_the_remedy(self, caplog):
        """A silent refusal is how this stays broken for another release."""
        loader = _loader()
        loader._responses = [[
            _cand("INCHIKEY:A", "Acetic acid", "HMDB0000042"),
            _cand("INCHIKEY:B", "Acetylglycine", "HMDB0000532"),
        ]]
        with caplog.at_level("WARNING"):
            loader._resolve_canonical_compound("acetate", explicit_ids={})
        assert "HMDB0000042" in caplog.text and "HMDB0000532" in caplog.text
        assert "CURATED_COMPOUND_HMDB_IDS" in caplog.text

    def test_ambiguity_at_name_tier_also_refuses(self, caplog):
        """Two exact name matches is a real collision, not a moiety artifact."""
        loader = _loader()
        loader._responses = [[
            _cand("INCHIKEY:L", "Lactate", "HMDB0000190", name_match=True),
            _cand("INCHIKEY:D", "Lactate", "HMDB0001311", name_match=True),
        ]]
        with caplog.at_level("WARNING"):
            assert loader._resolve_canonical_compound("lactate", explicit_ids={}) is None

    def test_pinned_names_are_exactly_the_ones_the_ladder_cannot_settle(self):
        """The map must stay minimal — an entry the ladder could resolve is
        an unnecessary hardcoding that will rot (#269/#271/#272).

        Measured over the real HMDB corpus: of 11 ambiguous curated names, 8 are
        settled by a unique exact-name match. Only these 3 have synonym-only
        candidates.
        """
        assert set(CURATED_COMPOUND_HMDB_IDS) == {"acetate", "lactate", "vitamin b12"}


class TestExplicitCurationDecisions:
    def test_explicit_id_resolves_ambiguity(self):
        """Curators decide what 'acetate' means; we don't infer it."""
        loader = _loader()
        loader._responses = [[
            _cand("INCHIKEY:ACID", "Acetic acid", "HMDB0000042"),
            _cand("INCHIKEY:GLY", "Acetylglycine", "HMDB0000532"),
        ]]
        assert loader._resolve_canonical_compound(
            "acetate", explicit_ids={"acetate": "HMDB0000042"}
        ) == "INCHIKEY:ACID"

    def test_explicit_id_that_matches_nothing_refuses(self, caplog):
        """A stale mapping must fail loudly, not silently pick the wrong twin."""
        loader = _loader()
        loader._responses = [[_cand("INCHIKEY:GLY", "Acetylglycine", "HMDB0000532")]]
        with caplog.at_level("WARNING"):
            assert loader._resolve_canonical_compound(
                "acetate", explicit_ids={"acetate": "HMDB0000042"}
            ) is None

    def test_map_keys_are_all_curated_names(self):
        """A key that isn't a curated metabolite is dead weight that will rot."""
        from database.ingestion.hmdb_loader import CURATED_MICROBIAL_NAMES

        for key in CURATED_COMPOUND_HMDB_IDS:
            assert key == key.lower().strip()
            assert key in CURATED_MICROBIAL_NAMES, (
                f"{key!r} is not in TAXON_METABOLITE_PRODUCERS — remove it"
            )

    def test_map_values_are_hmdb_accessions(self):
        for name, hmdb_id in CURATED_COMPOUND_HMDB_IDS.items():
            assert hmdb_id.startswith("HMDB"), f"{name} -> {hmdb_id!r}"


class TestQueryShape:
    def test_query_requires_an_identifier_and_searches_synonyms(self):
        loader = _loader()
        loader._responses = [[]]
        loader._resolve_canonical_compound("butyrate")
        cypher, params = loader._calls[0]
        assert "m.compound_id IS NOT NULL" in cypher, (
            "must only adopt identifier-keyed nodes, never another curated node"
        )
        assert "synonyms" in cypher, "synonyms are the bridge (#276)"
        assert "metabolite_id" not in cypher, (
            "the dead #271 predicate must not come back"
        )
        assert params["name"] == "butyrate"

    def test_matching_is_exact_not_substring(self):
        """'Indole' must not match 'Indole-3-carbinol' — that's the #277 bug."""
        loader = _loader()
        loader._responses = [[]]
        loader._resolve_canonical_compound("indole")
        cypher, _ = loader._calls[0]
        assert "CONTAINS" not in cypher.upper()
        assert "STARTS WITH" not in cypher.upper()
