from micromap_mapforge.confidence import Confidence
from micromap_mapforge.resolve.paper import PaperResolver


def make_resolver():
    return PaperResolver(
        identifier_fields=["pmid", "doi"],
        primary_id_field="pmid",
        identifier_index={
            "pmid": {"12345": {"node_id": "PMID:12345", "title": "Gut microbiome and IBD", "pmid": "12345"}},
            "doi": {"10.1000/abc": {"node_id": "DOI:10.1000/abc", "title": "Something", "doi": "10.1000/abc"}},
        },
        title_index={
            "gut microbiome and ibd": [{"node_id": "PMID:12345", "title": "Gut microbiome and IBD", "pmid": "12345"}],
            "microbial taxa in ulcerative colitis": [{"node_id": "PMID:98765", "title": "Microbial taxa in ulcerative colitis", "pmid": "98765"}],
        },
        fuzzy_threshold=0.92,
    )


def test_label():
    assert make_resolver().label == "Paper"


def test_pmid_match_extracted():
    r = make_resolver()
    cands = r.resolve("12345", {"source_field": "pmid"})
    assert cands[0].match_type == Confidence.EXTRACTED
    assert cands[0].node_id == "pmid:12345"
    assert cands[0].merge_field == "pmid"
    assert cands[0].merge_value == "12345"


def test_title_exact_match_inferred():
    r = make_resolver()
    cands = r.resolve("Gut Microbiome and IBD", {"source_field": "title"})
    assert len(cands) >= 1
    assert cands[0].match_type == Confidence.INFERRED
    assert cands[0].node_id == "pmid:12345"
    assert cands[0].merge_field == "pmid"
    assert cands[0].merge_value == "12345"


def test_title_fuzzy_above_threshold():
    r = make_resolver()
    # Minor punctuation difference — should still match above 0.92
    cands = r.resolve("microbial taxa in ulcerative colitis.", {"source_field": "title"})
    assert len(cands) >= 1
    assert cands[0].match_type == Confidence.INFERRED


def test_title_way_off_empty():
    r = make_resolver()
    assert r.resolve("completely different title about something else", {"source_field": "title"}) == []


def test_non_title_non_id_field_empty():
    r = make_resolver()
    assert r.resolve("12345", {"source_field": "random"}) == []
