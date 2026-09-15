"""#311: :Decision and :Contribution are MERGEd but never constrained.

Four shipped call sites MERGE these two labels — `api/routes/provenance_decisions.py`
(the REST ingest), and `micromap_mapforge.provenance.decision` /
`.contribution` (the MapForge submit path) — yet no uniqueness constraint on
either label exists in any code path. The only `CREATE CONSTRAINT decision_id`
in the repo is a snippet inside a markdown file. Without the constraint a
concurrent double-ingest creates duplicate nodes and the `MERGE` silently stops
being idempotent.

**The issue text asks for `:Contribution(id)`. That property does not exist.**
`ContributionRecord` has no `id` field and no writer sets one; the node's
identity is the composite `(organization_id, mapping_sha256, source_sha256)` it
is actually MERGEd on. A constraint on `Contribution.id` would be *vacuous* —
Neo4j ignores nodes missing a property in the key, so it would silently
constrain nothing, forever. That is precisely the rot behind #298 (`CHILD_OF`),
#304 (`HAS_GENE`) and #272 (`metabolite_id`): a guardrail pointed at a key
nothing writes, passing unconditionally because it can never bind.

So the tests below do not assert a hardcoded key. They parse the MERGE
statements out of the real writers and require the constraint key to equal the
merge key — if a writer re-keys, the constraint must follow or CI fails.

The two labels land in different places, deliberately:

  :Decision     -> bare `id`, alongside its #257 siblings (Experiment/Analysis/
                   Assertion) in `create_provenance_spine_schema`. The writers
                   MERGE on `{id}` alone, so a composite `(organization_id, id)`
                   would NOT match the merge key: ingesting org B's decision
                   would MATCH org A's node by id and overwrite its
                   organization_id, with no violation raised. The constraint has
                   to be the key the MERGE uses.
  :Contribution -> composite, in `UNIQUENESS_CONSTRAINTS`, which already
                   requires every entry be org-scoped. Its merge key genuinely
                   contains organization_id, so it fits that invariant.
"""
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from database.load_knowledge_graph import create_indexes_and_constraints


def _driver():
    """Mock driver that records every statement the schema path runs."""
    session = MagicMock()
    calls = []
    session.run = MagicMock(side_effect=lambda q, *a, **k: calls.append(q) or MagicMock())
    driver = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    driver.session.return_value = ctx
    return driver, calls


def _constraints_created():
    """Every CREATE CONSTRAINT the `--indexes` path runs, spine included.

    Goes through `create_indexes_and_constraints` rather than reading the
    constant, because production runs the function: a constraint declared in a
    list nothing executes is the #272 defect verbatim.
    """
    driver, calls = _driver()
    create_indexes_and_constraints(driver, "micromap")
    return [c for c in calls if "CREATE CONSTRAINT" in c]


def _constraint_for(label):
    matching = [
        c for c in _constraints_created()
        if re.search(rf"FOR \(\w+:{label}\)", c)
    ]
    assert matching, f":{label} has no uniqueness constraint (#311)"
    assert len(matching) == 1, f":{label} has {len(matching)} rival constraints"
    return matching[0]


def _constrained_keys(statement):
    """Property names in a REQUIRE clause, composite or single."""
    require = statement.split("REQUIRE", 1)[1]
    return {m.group(1) for m in re.finditer(r"\w+\.(\w+)", require)}


def _merge_keys(cypher, label):
    """Property names in the MERGE key of `label` — what identity really is."""
    m = re.search(rf"MERGE \(\w+:{label} \{{([^}}]*)\}}\)", cypher)
    assert m, f"no MERGE (:{label} {{...}}) found in:\n{cypher}"
    return {k.group(1) for k in re.finditer(r"(\w+)\s*:", m.group(1))}


def _mapforge_contribution_merge():
    from micromap_mapforge.provenance.contribution import (
        ContributionRecord,
        cypher_for_contribution,
    )

    rec = ContributionRecord(
        contributor="acme",
        reviewer="reviewer@example.com",
        organization_id="acme",
        source_sha256="a" * 64,
        mapping_sha256="b" * 64,
        destination="micromap-core",
        submitted_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
        resolved_count=1,
        unresolved_count=0,
        ambiguous_count=0,
    )
    return next(
        stmt for stmt, _ in cypher_for_contribution(rec)
        if "MERGE (c:Contribution" in stmt
    )


def _mapforge_decision_merge():
    from micromap_mapforge.provenance.decision import DecisionRecord, cypher_for_decision

    rec = DecisionRecord(
        id="dec-1",
        organization_id="acme",
        occurred_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
        summary="s",
    )
    return next(
        stmt for stmt, _ in cypher_for_decision(rec)
        if "MERGE (d:Decision" in stmt
    )


def _rest_decision_merge():
    """The REST writer's MERGE, read from source — it is an inline literal."""
    src = Path(__file__).resolve().parents[1] / "api" / "routes" / "provenance_decisions.py"
    text = src.read_text(encoding="utf-8")
    assert "MERGE (d:Decision" in text, "REST decision writer moved; update this test"
    return text


class TestTheConstraintsExist:
    """The whole point of #311. Both fail before the fix."""

    def test_decision_is_constrained(self):
        _constraint_for("Decision")

    def test_contribution_is_constrained(self):
        _constraint_for("Contribution")


class TestKeysMatchWhatIsActuallyMerged:
    """Anti-rot: a constraint on a property no writer sets constrains nothing."""

    def test_contribution_is_not_keyed_on_id(self):
        """The issue asked for `:Contribution(id)`. No writer sets `id`.

        Neo4j skips nodes missing a key property, so this constraint would
        silently apply to zero nodes — a guardrail that can never fire, the
        #298 / #304 failure mode.
        """
        keys = _constrained_keys(_constraint_for("Contribution"))
        assert "id" not in keys, (
            "Contribution.id is written by nobody — ContributionRecord has no "
            "such field. This constraint would bind to zero nodes forever."
        )

    def test_contribution_constraint_key_is_the_merge_key(self):
        merge = _merge_keys(_mapforge_contribution_merge(), "Contribution")
        constrained = _constrained_keys(_constraint_for("Contribution"))
        assert constrained == merge, (
            f"constraint key {sorted(constrained)} != merge key {sorted(merge)}; "
            "a constraint that is not the merge key does not make MERGE idempotent"
        )

    def test_decision_constraint_key_is_the_merge_key(self):
        merge = _merge_keys(_mapforge_decision_merge(), "Decision")
        constrained = _constrained_keys(_constraint_for("Decision"))
        assert constrained == merge == {"id"}, (
            f"constraint key {sorted(constrained)} != merge key {sorted(merge)}"
        )

    def test_both_decision_writers_merge_on_the_same_key(self):
        """REST and MapForge must agree, or one of them defeats the constraint."""
        assert re.search(r"MERGE \(d:Decision \{id: \$id\}\)", _rest_decision_merge()), (
            "the REST writer no longer MERGEs :Decision on {id} alone — the "
            "constraint key must be re-derived from both writers"
        )


class TestMultiTenancyReasoning:
    def test_contribution_constraint_is_org_scoped(self):
        """Its merge key really does carry organization_id, so it must be
        composite — two orgs may legitimately submit the same source+mapping."""
        assert "organization_id" in _constrained_keys(_constraint_for("Contribution"))

    def test_decision_constraint_is_bare_id(self):
        """NOT composite, deliberately — see this module's docstring. A
        composite key would not match `MERGE (d:Decision {id: $id})`, letting a
        second org silently take over an existing decision node by id."""
        assert _constrained_keys(_constraint_for("Decision")) == {"id"}


class TestFailuresAreVisible:
    """#272's lesson: a swallowed constraint failure is an absent constraint.

    The spine path logged every failure at `warning` and returned nothing, so a
    Decision constraint rejected because duplicates already exist would be
    invisible — and the acceptance criterion is that this is safe to run on a
    database that already has duplicates.
    """

    def test_spine_constraint_failure_is_loud_and_counted(self, caplog):
        session = MagicMock()

        def run(q, *a, **k):
            if "CREATE CONSTRAINT" in q and ":Decision" in q:
                raise RuntimeError("would violate uniqueness: 2 nodes share id")
            return MagicMock()

        session.run = MagicMock(side_effect=run)
        driver = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=False)
        driver.session.return_value = ctx

        with caplog.at_level("ERROR"):
            report = create_indexes_and_constraints(driver, "micromap")

        assert "uniqueness" in caplog.text.lower(), (
            "a Decision constraint rejected for pre-existing duplicates must be "
            "logged at ERROR, not buried at warning"
        )
        assert report["constraints_failed"] >= 1, (
            "spine constraint failures must be counted in the report, or a "
            "deploy gate cannot see them"
        )


class TestIdempotent:
    @pytest.mark.parametrize("label", ["Decision", "Contribution"])
    def test_creation_is_guarded_by_if_not_exists(self, label):
        assert "IF NOT EXISTS" in _constraint_for(label)
