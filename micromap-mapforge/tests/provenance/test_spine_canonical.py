"""#284: canonical-org policy for the provenance spine.

`canonical_orgs()` names the orgs whose assertions represent shared/reference
truth and may therefore project a concrete edge into the graph that *every*
caller reads. Non-canonical (customer / eval / rehearsal) assertions still get
full lineage, but must not mutate the shared reference edges — that leak is
exactly what surfaced the `demo`/`intrinsic-eval` rehearsal records above
curated Disbiome evidence (#284).
"""
import pytest

from micromap_mapforge.provenance.spine import canonical_orgs, is_canonical_org


def test_canonical_orgs_defaults_to_default(monkeypatch):
    monkeypatch.delenv("CANONICAL_ORGS", raising=False)
    monkeypatch.delenv("PUBLIC_ORGS", raising=False)
    assert canonical_orgs() == frozenset({"default"})


def test_canonical_orgs_reads_env(monkeypatch):
    monkeypatch.setenv("CANONICAL_ORGS", "default, reference , curated")
    assert canonical_orgs() == frozenset({"default", "reference", "curated"})


def test_canonical_orgs_falls_back_to_public_orgs(monkeypatch):
    # Mirrors the read API's PUBLIC_ORGS (api/scoping.get_public_orgs) so a single
    # env can govern both what's publicly readable and what may project.
    monkeypatch.delenv("CANONICAL_ORGS", raising=False)
    monkeypatch.setenv("PUBLIC_ORGS", "default,shared")
    assert canonical_orgs() == frozenset({"default", "shared"})


def test_canonical_orgs_empty_env_is_default(monkeypatch):
    monkeypatch.setenv("CANONICAL_ORGS", "  , ")
    assert canonical_orgs() == frozenset({"default"})


@pytest.mark.parametrize("org,canon,expected", [
    ("default", frozenset({"default"}), True),
    ("demo", frozenset({"default"}), False),
    ("intrinsic-eval", frozenset({"default"}), False),
    ("reference", frozenset({"default", "reference"}), True),
])
def test_is_canonical_org(org, canon, expected):
    assert is_canonical_org(org, canon) is expected


def test_is_canonical_org_resolves_env_when_canon_omitted(monkeypatch):
    monkeypatch.setenv("CANONICAL_ORGS", "acme")
    assert is_canonical_org("acme") is True
    assert is_canonical_org("demo") is False
