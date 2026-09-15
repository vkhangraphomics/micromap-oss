"""
Tests for the curated PRODUCES loader.

Covers:
- Metabolite-dedup branching introduced for issue #43
- Rank-aware Taxon matching introduced for issue #48
"""

from unittest.mock import MagicMock

import pytest

from database.ingestion.produces_loader import ProducesLoader


@pytest.fixture
def loader():
    """ProducesLoader with a mock driver and patched execute_cypher.

    Each test's _execute_calls list captures (cypher, params) pairs in order,
    and _execute_responses is a list of canned return values consumed FIFO.
    """
    driver = MagicMock()
    inst = ProducesLoader(driver=driver, organization_id="test-org")
    inst._execute_calls = []
    inst._execute_responses = []

    def fake_execute(cypher, params=None, write=True):
        inst._execute_calls.append((cypher, params or {}))
        if inst._execute_responses:
            return inst._execute_responses.pop(0)
        return []

    inst.execute_cypher = fake_execute
    return inst


def _record(taxon_pattern="Faecalibacterium", metabolite="butyrate"):
    return {
        "taxon_pattern": taxon_pattern,
        "metabolite_name": metabolite.lower(),
        "metabolite_display": metabolite.title(),
        "evidence_level": "high",
        "notes": "test",
    }


def _produces_cypher(loader_instance) -> str:
    """The PRODUCES MERGE is always the last execute_cypher call in load_batch."""
    return loader_instance._execute_calls[-1][0]


# ---------------------------------------------------------------------------
# Issue #43 — metabolite-dedup branching
# ---------------------------------------------------------------------------


def test_creates_name_lower_node_when_no_existing_metabolite(loader):
    """Legacy path: no HMDB/KEGG node exists, MERGE on name_lower."""
    # Step-1 lookup returns 0 matches; step-2 takes the create branch.
    loader._execute_responses = [
        [],  # resolution: no candidates -> curated fallback
        [],                    # MERGE (return value unused)
        [{"count": 1}],        # PRODUCES MERGE
    ]

    loader.load_batch([_record()])

    cyphers = [c for c, _ in loader._execute_calls]
    # Three queries: existence check, MERGE name_lower, PRODUCES MERGE.
    assert len(cyphers) == 3
    assert "MERGE (m:Compound {name_lower:" in cyphers[1]
    assert "MERGE (t)-[r:PRODUCES]->(m)" in cyphers[2]


def test_reuses_existing_identifier_keyed_node_when_resolvable(loader):
    """The #276/#271 path: a curated claim adopts the HMDB-keyed compound.

    'butyrate' resolves to Butyric acid (HMDB0000039) via its real HMDB synonym,
    and that node gets tagged with name_lower so the PRODUCES MERGE lands on it
    instead of on a disjoint curated node.
    """
    loader._execute_responses = [
        [{  # resolution: exactly one candidate, matched by synonym
            "compound_id": "INCHIKEY:FERIUCNNQQJTOY-UHFFFAOYSA-N",
            "name": "Butyric acid", "hmdb_id": "HMDB0000039",
            "name_match": False, "iupac_match": False,
        }],
        [],              # tagging UPDATE (return value unused)
        [{"count": 1}],  # PRODUCES MERGE
    ]

    loader.load_batch([_record()])

    cyphers = [c for c, _ in loader._execute_calls]
    params = [p for _, p in loader._execute_calls]
    assert len(cyphers) == 3
    # The middle call SETs name_lower on the resolved node, keyed by compound_id
    # — never by the name predicate, which would tag every ambiguous candidate.
    assert "SET m.name_lower" in cyphers[1]
    assert "MATCH (m:Compound {compound_id: $compound_id})" in cyphers[1]
    assert params[1]["compound_id"] == "INCHIKEY:FERIUCNNQQJTOY-UHFFFAOYSA-N"
    assert "MERGE (m:Compound {name_lower:" not in cyphers[1], (
        "must adopt the existing node, not create a curated one"
    )
    assert "MERGE (t)-[r:PRODUCES]->(m)" in cyphers[2]


def test_ambiguous_resolution_falls_back_to_curated_node(loader):
    """REGRESSION (#276): refuse rather than hang 43K edges on the wrong molecule.

    Two synonym-only candidates and no curation decision -> the safe legacy
    path, not a guess.
    """
    loader._execute_responses = [
        [
            {"compound_id": "INCHIKEY:A", "name": "Acetic acid",
             "hmdb_id": "HMDB0000042", "name_match": False, "iupac_match": False},
            {"compound_id": "INCHIKEY:B", "name": "Acetylglycine",
             "hmdb_id": "HMDB0000532", "name_match": False, "iupac_match": False},
        ],
        [],
        [{"count": 1}],
    ]

    loader.load_batch([_record(metabolite="some-uncurated-name")])

    cyphers = [c for c, _ in loader._execute_calls]
    assert "MERGE (m:Compound {name_lower:" in cyphers[1], (
        "ambiguity must fall back to the curated node, never pick a candidate"
    )


def test_resolution_requires_an_identifier_keyed_node(loader):
    """Step-1 lookup must require m.compound_id IS NOT NULL.

    A name_lower-keyed curated node from a prior partial run must NOT trigger
    the dedup branch; only HMDB/KEGG/Reactome-loaded nodes should.

    This asserted `m.metabolite_id IS NOT NULL` until #276 — which is the bug,
    not the contract. Nothing has written metabolite_id since the #146 label
    migration, so the predicate was always false: the branch was dead, every
    curated claim created a disjoint node, and the graph ended up with zero
    taxon->compound->disease paths (#271). The *intent* here was always right;
    only the key was stale. Guard it against coming back.
    """
    loader._execute_responses = [
        [],  # resolution: no candidates
        [],
        [{"count": 1}],
    ]

    loader.load_batch([_record()])

    resolution_cypher = loader._execute_calls[0][0]
    assert "m.compound_id IS NOT NULL" in resolution_cypher
    assert "metabolite_id" not in resolution_cypher, (
        "the dead #271 predicate must not return"
    )


# ---------------------------------------------------------------------------
# Issue #48 — rank-aware Taxon matching in the PRODUCES Cypher
# ---------------------------------------------------------------------------


def _setup_three_call_flow(loader):
    """Pre-populate execute_responses for the existence-check + MERGE + PRODUCES sequence."""
    loader._execute_responses = [
        [],  # resolution: no candidates
        [],
        [{"count": 1}],
    ]


def test_produces_cypher_uses_rank_aware_genus_match(loader):
    """Genus-level fan-out is restricted to t.rank = 'species'."""
    _setup_three_call_flow(loader)
    loader.load_batch([_record(taxon_pattern="Faecalibacterium")])

    cypher = _produces_cypher(loader)
    assert "t.genus = $taxon_pattern AND t.rank = 'species'" in cypher


def test_produces_cypher_strain_fanout_uses_space_delimiter(loader):
    """Species-level fan-out to strains requires a space-delimited prefix."""
    _setup_three_call_flow(loader)
    loader.load_batch([_record(taxon_pattern="Faecalibacterium prausnitzii")])

    cypher = _produces_cypher(loader)
    assert "t.name STARTS WITH $taxon_pattern + ' '" in cypher
    assert "t.rank IN ['strain', 'subspecies', 'no rank']" in cypher


def test_produces_cypher_no_longer_uses_unbounded_prefix(loader):
    """The original `t.name STARTS WITH $taxon_pattern` (unbounded) is gone.

    That clause matched sister taxa whose names happened to share a prefix
    (issue #48). The new query requires either an exact name match, a
    genus-level fan-out, or a space-delimited strain prefix.
    """
    _setup_three_call_flow(loader)
    loader.load_batch([_record()])

    cypher = _produces_cypher(loader)
    # The exact unbounded clause from the old query must not appear.
    assert "t.name STARTS WITH $taxon_pattern\n" not in cypher
    # The bare `t.genus = $taxon_pattern` (without rank guard) must not appear.
    assert "OR t.genus = $taxon_pattern\n" not in cypher


def test_metabolite_cypher_uses_compound_label():
    """produces_loader must write :Compound nodes, not :Metabolite."""
    import inspect as ins
    from database.ingestion.produces_loader import ProducesLoader
    source = ins.getsource(ProducesLoader)
    assert ":Compound" in source
    assert ":Metabolite" not in source
