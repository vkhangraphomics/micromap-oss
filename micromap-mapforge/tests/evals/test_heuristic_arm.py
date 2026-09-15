"""#266: the heuristic mapping arm, scored against the curated gold bundles.

Offline and deterministic (no LLM), so it runs in CI as a regression guard: a
schema_config hint change that degrades entity recovery, or a regression that
lets the heuristic start emitting relationships, fails here. The LLM arm lives in
its own opt-in module (it needs a key and is non-deterministic).
"""
import pytest

from micromap_mapforge.evals.cases import default_cases
from micromap_mapforge.evals.run import run_all, run_heuristic

CASES = default_cases()


@pytest.fixture(scope="module")
def gold_bundles_present():
    missing = [c.name for c in CASES if not (c.source_csv.exists() and c.gold_mapping.exists())]
    if missing:
        pytest.skip(f"gold bundles missing: {missing}")


pytestmark = pytest.mark.usefixtures("gold_bundles_present")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_heuristic_recovers_entity_labels_strongly(case):
    """The offline heuristic should recover (nearly) all gold entity labels — its
    core strength, and the reason it's the sensible default for scaffolding."""
    res = run_heuristic(case)
    assert res.score is not None
    assert res.score.entities.f1 >= 0.9, (
        f"{case.name}: heuristic entity F1 {res.score.entities.f1:.2f} < 0.9 — "
        f"a hint regression likely dropped a label"
    )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_heuristic_never_invents_relationships(case):
    """Structural invariant: draft_heuristic_mapping always emits relationships:[]
    (every gold here has relationships), so its relationship recall is 0. This is
    the precise gap the LLM arm exists to fill — the eval makes it a hard fact."""
    res = run_heuristic(case)
    assert res.n_relationships == 0
    assert res.score.relationships.recall == 0.0


def test_run_all_heuristic_only_skips_llm():
    results = run_all(include_llm=False, cases=CASES)
    assert len(results) == len(CASES)
    assert all(r.llm is None for r in results)
    assert all(r.heuristic.score is not None for r in results)


def test_at_least_one_conventional_and_one_relationship_heavy_case():
    """#266 acceptance: the fixture set spans both regimes it asks about."""
    assert any(c.columns_regime == "conventional" for c in CASES)
    assert all(  # every case's gold carries relationships → the heuristic gap shows
        run_heuristic(c).score.relationships.recall == 0.0 for c in CASES
    )
