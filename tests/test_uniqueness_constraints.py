"""#272: core entities had NO uniqueness constraints.

Live kgdev carried exactly 3 uniqueness constraints — Analysis.id, Assertion.id,
Experiment.id — all from the #257 provenance spine, which is created by a
*different* code path. Taxon, Compound, Disease, Paper, Drug, Pathway, Gene and
Protein had none, so nothing at the schema level objected to the duplicate
`:Compound` identities behind #267/#271/#281.

Three stacked failures caused it:

1. `create_indexes_and_constraints` — the `--indexes` path, the only schema step
   production actually runs — contained ONLY `CREATE INDEX`. Its name promised
   constraints it never created, which is why nobody noticed.
2. `GraphomicsSchema._create_constraints` DID declare them, but its only caller
   was `neo4j_schema.py`'s own `__main__` block — dead code — which additionally
   defaulted to the Fabric composite `graphomics`, where constraints cannot be
   created at all.
3. Even had it run, `metabolite_id_unique` targeted `:Metabolite` — 0 nodes since
   the #146 rename — so `:Compound` would STILL be unconstrained. Same rot as
   #269/#271/#277: the migration moved labels but not the keys around them.

Keys are composite on `organization_id` deliberately. Live has 4 orgs (default,
graphomics, intrinsic-eval, demo). A bare `Taxon.taxon_id` constraint is unique
today only because all reference data is `default`; it would block a tenant
loading its own copy tomorrow. The existing Disease index is already
`(organization_id, name_normalized)` — that is the intended key.
"""
from unittest.mock import MagicMock

import pytest

from database.load_knowledge_graph import (
    UNIQUENESS_CONSTRAINTS,
    create_indexes_and_constraints,
)


def _driver():
    session = MagicMock()
    calls = []

    def run(q, *a, **k):
        calls.append(q)
        return MagicMock()

    session.run = MagicMock(side_effect=run)
    driver = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    driver.session.return_value = ctx
    return driver, calls


class TestConstraintsAreActuallyCreated:
    def test_the_indexes_path_now_creates_constraints(self):
        """REGRESSION (#272): `create_indexes_and_constraints` created ZERO."""
        driver, calls = _driver()
        create_indexes_and_constraints(driver, "micromap")
        created = [c for c in calls if "CREATE CONSTRAINT" in c]
        assert created, (
            "the function named create_indexes_and_constraints created no "
            "constraints — the exact #272 bug"
        )

    @pytest.mark.parametrize("label,key", [
        ("Taxon", "taxon_id"),
        ("Compound", "compound_id"),
        ("Disease", "name_normalized"),
        ("Paper", "pmid"),
        ("Drug", "drug_id"),
        ("Pathway", "pathway_id"),
        ("Protein", "protein_id"),
        ("Gene", "name"),
    ])
    def test_core_entity_is_constrained_on_its_real_key(self, label, key):
        driver, calls = _driver()
        create_indexes_and_constraints(driver, "micromap")
        text = "\n".join(c for c in calls if "CREATE CONSTRAINT" in c)
        assert f"(n:{label})" in text, f"{label} has no uniqueness constraint"
        assert f"n.{key}" in text, f"{label} is not constrained on {key}"


class TestKeysMatchReality:
    def test_compound_is_keyed_on_compound_id_not_metabolite_id(self):
        """The #146 rot: metabolite_id_unique targeted a label with 0 nodes.

        Live: 0 compounds have `metabolite_id`; 2,786 have `compound_id`.
        """
        text = "\n".join(q for _, _, q in _constraint_specs())
        assert "metabolite_id" not in text, (
            "metabolite_id has not been written by any loader since #146"
        )
        assert ":Metabolite" not in text, ":Metabolite has had 0 nodes since #146"

    def test_gene_is_keyed_on_name_not_gene_id(self):
        """derive_spine keys :Gene on `name`; live has 545 nodes, all gene_id NULL."""
        specs = {label: key for label, key, _ in _constraint_specs()}
        assert specs["Gene"] == "name"

    def test_disease_is_not_keyed_on_disease_id(self):
        """All 7 disease loaders MERGE on name_normalized; disease_id is often
        null (11 of 464 live). A disease_id constraint would fire spuriously —
        the reasoning documented in neo4j_schema.py and issue #44 still holds."""
        specs = {label: key for label, key, _ in _constraint_specs()}
        assert specs["Disease"] == "name_normalized"


class TestMultiTenantSafety:
    def test_every_constraint_is_composite_on_organization_id(self):
        """Live has 4 orgs. A bare key would block a tenant loading its own copy
        of reference data — unique today only because it's all `default`."""
        for label, key, q in _constraint_specs():
            assert "n.organization_id" in q, (
                f"{label}.{key} is not scoped by organization_id — this would "
                f"break multi-tenancy the moment a second org loads it"
            )

    def test_provenance_constraints_are_left_alone(self):
        """Analysis/Assertion/Experiment already exist on bare `id` (#257) and
        legitimately span orgs (demo, intrinsic-eval). Don't redefine them."""
        labels = {label for label, _, _ in _constraint_specs()}
        assert not (labels & {"Analysis", "Assertion", "Experiment"})


class TestRedundantIndexIsRetired:
    """REGRESSION (#272): a plain index on the same key BLOCKS the constraint.

    Live refused it outright:

        There already exists an index (:Disease {organization_id, name_normalized}).
        A constraint cannot be created until the index has been dropped.

    `disease_org_name_normalized` was created by this very function on exactly
    the key the Disease constraint needs, so the constraint would have failed on
    every single run. A uniqueness constraint brings its own backing index, which
    serves the same lookups — the plain one is now redundant, not merely in the
    way.
    """

    def test_the_conflicting_index_is_no_longer_created(self):
        driver, calls = _driver()
        create_indexes_and_constraints(driver, "micromap")
        created = "\n".join(c for c in calls if "CREATE INDEX" in c)
        assert "disease_org_name_normalized" not in created, (
            "recreating this index guarantees the Disease constraint fails"
        )

    def test_the_legacy_index_is_dropped_before_constraints(self):
        """Existing databases already have it; it must be cleaned up, and before
        the constraint is attempted."""
        driver, calls = _driver()
        create_indexes_and_constraints(driver, "micromap")
        drop_at = next(
            (i for i, c in enumerate(calls)
             if "DROP INDEX" in c and "disease_org_name_normalized" in c), None
        )
        assert drop_at is not None, "legacy index must be dropped"
        con_at = next(i for i, c in enumerate(calls) if "CREATE CONSTRAINT" in c)
        assert drop_at < con_at, "drop must precede constraint creation"

    def test_drop_is_idempotent(self):
        driver, calls = _driver()
        create_indexes_and_constraints(driver, "micromap")
        drop = next(c for c in calls if "DROP INDEX" in c)
        assert "IF EXISTS" in drop


class TestFailuresAreVisible:
    """The counts are `>=` len(UNIQUENESS_CONSTRAINTS), not `==`, since #311:
    the report now also folds in the bare-`id` provenance constraints created by
    create_provenance_spine_schema (Experiment/Analysis/Assertion from #257,
    Decision from #311). A deploy gating on `constraints_failed` has to see every
    constraint failure, not only the ones declared in this module's list."""

    def test_constraint_failures_are_not_silently_swallowed(self, caplog):
        """A constraint failing because duplicates exist is exactly when you need
        to hear about it. The old code logged index failures at warning and
        buried them."""
        session = MagicMock()

        def run(q, *a, **k):
            if "CREATE CONSTRAINT" in q:
                raise RuntimeError("would violate uniqueness: 2 nodes share key")
            return MagicMock()

        session.run = MagicMock(side_effect=run)
        driver = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=False)
        driver.session.return_value = ctx

        with caplog.at_level("ERROR"):
            report = create_indexes_and_constraints(driver, "micromap")

        assert "uniqueness" in caplog.text.lower()
        assert report["constraints_created"] == 0
        assert report["constraints_failed"] >= len(UNIQUENESS_CONSTRAINTS)

    def test_reports_what_it_did(self):
        driver, _ = _driver()
        report = create_indexes_and_constraints(driver, "micromap")
        assert report["constraints_failed"] == 0
        assert report["constraints_created"] >= len(UNIQUENESS_CONSTRAINTS)


class TestNoDeadDuplicate:
    def test_the_dead_schema_class_is_gone(self):
        """#272: 'do not leave both'. GraphomicsSchema._create_constraints
        declared a rival, staler constraint list reachable only from a __main__
        block that pointed at the Fabric composite. Two sources of truth for the
        schema is how the stale one goes unnoticed for months."""
        import database.neo4j_schema as schema

        assert not hasattr(schema, "GraphomicsSchema")
        assert not hasattr(schema, "GraphomicsSchemaManager")

    def test_provenance_spine_schema_survives(self):
        """It is the live path that created the only 3 constraints kgdev had."""
        from database.neo4j_schema import create_provenance_spine_schema

        assert callable(create_provenance_spine_schema)


def _constraint_specs():
    """(label, key, statement) for each declared uniqueness constraint."""
    import re

    out = []
    for q in UNIQUENESS_CONSTRAINTS:
        m = re.search(r"FOR \(n:(\w+)\)", q)
        keys = re.findall(r"n\.(\w+)", q)
        non_org = [k for k in keys if k != "organization_id"]
        out.append((m.group(1), non_org[0] if non_org else None, q))
    return out
