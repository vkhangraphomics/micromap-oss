"""#281: migrate legacy curated compounds onto their canonical HMDB twins.

The curated PRODUCES data created identifier-less `name_lower` nodes ("Butyrate")
that hold all 218,454 producer edges, while HMDB's twin ("Butyric acid") holds
the disease/pathway links. #276 gave us a working resolver; this moves the edges
so the two halves finally meet.

Why a migration and not a re-load: the PRODUCES MERGE keys on `name_lower`
(produces_loader.py:1069). The resolver tags the canonical node with the SAME
`name_lower`, so until the legacy node loses it, that MERGE matches BOTH and
`--produces` doubles the edges (218,454 -> ~436K) instead of reconnecting them.
Stripping `name_lower` from the legacy node is the load-bearing step.

Live shape this is written against (kgdev, 2026-07-15):
  47 legacy nodes have name_lower, all with compound_id IS NULL
  44 of them hold PRODUCES (218,454 edges); PRODUCES is their ONLY edge type
  3 have no edges and no HMDB twin (amuc_1100, antimicrobial peptides,
    isourolithin a) -> must be left alone
"""
from unittest.mock import MagicMock

from database.migrate_curated_compounds import migrate


class _Recorder:
    """Captures the Cypher a migration run issues, with canned responses."""

    def __init__(self, responses=None):
        self.calls = []
        self._responses = list(responses or [])

    def __call__(self, cypher, params=None, **kw):
        self.calls.append((cypher, params or {}))
        return self._responses.pop(0) if self._responses else []

    def cyphers(self):
        return [c for c, _ in self.calls]

    def joined(self):
        return "\n".join(self.cyphers())


def _driver(responses=None):
    rec = _Recorder(responses)
    session = MagicMock()

    def run(cypher, **params):
        rows = rec(cypher, params)
        result = MagicMock()
        result.__iter__ = MagicMock(return_value=iter([_row(r) for r in rows]))
        result.single.return_value = _row(rows[0]) if rows else None
        return result

    session.run = MagicMock(side_effect=run)
    driver = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    driver.session.return_value = ctx
    return driver, rec


def _row(d):
    m = MagicMock()
    m.__getitem__ = lambda self, k, _d=d: _d[k]
    m.get = lambda k, default=None, _d=d: _d.get(k, default)
    m.data = lambda _d=d: _d
    return m


def _legacy_row(name_lower="butyrate", edges=5553, candidates=None):
    """A row in the shape _SURVEY actually returns: candidate arrays, unresolved."""
    candidates = candidates if candidates is not None else [
        {"id": "INCHIKEY:FERIUCNNQQJTOY-UHFFFAOYSA-N", "hmdb": "HMDB0000039",
         "name": "Butyric acid", "status": "quantified",
         "name_match": False, "iupac_match": False},
    ]
    return {
        "name_lower": name_lower,
        "legacy_id": f"4:abc:{name_lower}",
        "edges": edges,
        "candidate_ids": [c["id"] for c in candidates],
        "candidate_hmdb_ids": [c.get("hmdb") for c in candidates],
        "candidate_names": [c["name"] for c in candidates],
        "candidate_status": [c["status"] for c in candidates],
        "candidate_name_match": [c["name_match"] for c in candidates],
        "candidate_iupac_match": [c["iupac_match"] for c in candidates],
    }


LEGACY_ROW = _legacy_row()


def _APPLY_RESPONSES(moved=5553, deleted=1):
    """Canned responses for one apply pass, in the order migrate() issues them:
    survey -> move (consumed) -> count_moved -> delete_legacy -> tag_canonical."""
    return [
        [LEGACY_ROW],        # _SURVEY
        [],                  # _MOVE (.consume(), return unused)
        [{"moved": moved}],  # _COUNT_MOVED
        [{"deleted": deleted}],  # _DELETE_LEGACY
        [{"tagged": 1}],     # _TAG_CANONICAL
    ]


class TestDryRunIsReadOnly:
    def test_dry_run_issues_no_writes(self):
        """A dry run must not touch the graph — this moves 218K edges."""
        driver, rec = _driver([[LEGACY_ROW]])
        migrate(driver, "micromap", dry_run=True)
        text = rec.joined().upper()
        for verb in ("DELETE", "MERGE", "SET ", "CREATE", "REMOVE"):
            assert verb not in text, f"dry run issued a {verb.strip()}"

    def test_dry_run_reports_what_it_would_move(self):
        driver, _ = _driver([[LEGACY_ROW]])
        report = migrate(driver, "micromap", dry_run=True)
        assert report["dry_run"] is True
        assert report["planned"][0]["name_lower"] == "butyrate"
        assert report["planned"][0]["edges"] == 5553
        assert report["planned"][0]["canonical_name"] == "Butyric acid"
        assert report["total_edges_to_move"] == 5553

    def test_dry_run_default(self):
        """Destructive by omission is how accidents happen."""
        driver, rec = _driver([[LEGACY_ROW]])
        report = migrate(driver, "micromap")
        assert report["dry_run"] is True
        assert "DELETE" not in rec.joined().upper()


class TestOnlyTargetsRealLegacyNodes:
    def test_selection_requires_null_compound_id_and_name_lower(self):
        driver, rec = _driver([[]])
        migrate(driver, "micromap", dry_run=True)
        q = rec.cyphers()[0]
        assert "compound_id IS NULL" in q, (
            "must never adopt/delete an identifier-keyed compound"
        )
        assert "name_lower IS NOT NULL" in q

    def test_selection_excludes_the_audit_write_compounds(self):
        """12 NULL-id nodes came from an out-of-band write (HMOs, #269) and have
        pubchem_cid but no name_lower. They are NOT curated_produces nodes."""
        driver, rec = _driver([[]])
        migrate(driver, "micromap", dry_run=True)
        q = rec.cyphers()[0]
        assert "curated_produces" in q, (
            "must scope to source='curated_produces', or the #269 audit-write "
            "HMO compounds get swept up"
        )

    def test_unresolvable_names_are_skipped_not_deleted(self):
        """amuc_1100 / antimicrobial peptides / isourolithin a have no twin."""
        driver, rec = _driver([[_legacy_row("amuc_1100", edges=0, candidates=[])]])
        report = migrate(driver, "micromap", dry_run=False)
        assert report["skipped"] == ["amuc_1100"]
        assert report["migrated"] == []
        assert "DETACH DELETE" not in rec.joined(), (
            "a node with no canonical twin must never be deleted"
        )

    def test_pinned_names_resolve_via_the_curation_decision(self):
        """REGRESSION (#281): the migration must honour CURATED_COMPOUND_HMDB_IDS.

        Caught by the live dry-run: reimplementing #276's ladder without the pins
        silently SKIPPED acetate, lactate and vitamin b12 — 62,928 edges,
        including acetate's 43,108 (the largest bucket in the graph). Those three
        are pinned precisely BECAUSE the ladder can't settle them.
        """
        driver, _ = _driver([[_legacy_row("acetate", edges=43108, candidates=[
            {"id": "INCHIKEY:A", "hmdb": "HMDB0000042", "name": "Acetic acid",
             "status": "quantified", "name_match": False, "iupac_match": False},
            {"id": "INCHIKEY:B", "hmdb": "HMDB0000532", "name": "Acetylglycine",
             "status": "quantified", "name_match": False, "iupac_match": False},
        ])]])
        report = migrate(driver, "micromap", dry_run=True)
        assert report["skipped"] == []
        assert report["planned"][0]["canonical_name"] == "Acetic acid", (
            "acetate is pinned to HMDB0000042 — must not resolve to Acetylglycine"
        )
        assert report["total_edges_to_move"] == 43108

    def test_ambiguous_and_unpinned_is_skipped_not_guessed(self):
        """An ambiguous name with no curation decision must refuse."""
        driver, rec = _driver([[_legacy_row("some-new-metabolite", edges=99,
                                            candidates=[
            {"id": "INCHIKEY:A", "hmdb": "HMDB0000001", "name": "Thing A",
             "status": "quantified", "name_match": False, "iupac_match": False},
            {"id": "INCHIKEY:B", "hmdb": "HMDB0000002", "name": "Thing B",
             "status": "quantified", "name_match": False, "iupac_match": False},
        ])]])
        report = migrate(driver, "micromap", dry_run=False)
        assert report["skipped"] == ["some-new-metabolite"]
        assert "DETACH DELETE" not in rec.joined()

    def test_stale_pin_is_skipped_not_silently_mismatched(self):
        """A pin naming an hmdb_id no candidate has must fail loudly, not guess."""
        driver, _ = _driver([[_legacy_row("acetate", edges=43108, candidates=[
            {"id": "INCHIKEY:B", "hmdb": "HMDB0000532", "name": "Acetylglycine",
             "status": "quantified", "name_match": False, "iupac_match": False},
        ])]])
        report = migrate(driver, "micromap", dry_run=True)
        assert report["skipped"] == ["acetate"]

    def test_stub_loses_to_real_evidence(self):
        """formate: HMDB0304356 [expected] must not beat Formic acid [quantified]."""
        driver, _ = _driver([[_legacy_row("formate", edges=4376, candidates=[
            {"id": "INCHIKEY:STUB", "name": "formate", "status": "expected",
             "name_match": True, "iupac_match": False},
            {"id": "INCHIKEY:REAL", "name": "Formic acid", "status": "quantified",
             "name_match": False, "iupac_match": False},
        ])]])
        report = migrate(driver, "micromap", dry_run=True)
        assert report["planned"][0]["canonical_name"] == "Formic acid"

    def test_real_name_match_is_not_stolen_by_better_evidenced_synonym(self):
        """vitamin k2 (37,832 edges) must not migrate onto Menadione (vit K3)."""
        driver, _ = _driver([[_legacy_row("vitamin k2", edges=37832, candidates=[
            {"id": "INCHIKEY:K2", "name": "vitamin K2", "status": "detected",
             "name_match": True, "iupac_match": False},
            {"id": "INCHIKEY:K3", "name": "Menadione", "status": "quantified",
             "name_match": False, "iupac_match": False},
        ])]])
        report = migrate(driver, "micromap", dry_run=True)
        assert report["planned"][0]["canonical_name"] == "vitamin K2"


class TestApplyOrdering:
    def test_strips_name_lower_from_legacy(self):
        """THE load-bearing step: without it --produces re-fans forever."""
        driver, rec = _driver(_APPLY_RESPONSES())
        migrate(driver, "micromap", dry_run=False)
        text = rec.joined()
        assert "DETACH DELETE" in text, "legacy node must be removed once emptied"

    def test_canonical_is_tagged_by_compound_id_not_name(self):
        driver, rec = _driver(_APPLY_RESPONSES())
        migrate(driver, "micromap", dry_run=False)
        tag = [c for c in rec.cyphers() if "name_lower = $name_lower" in c]
        assert tag, "canonical node must gain name_lower"
        assert "{compound_id: $canonical_id}" in tag[0], (
            "tag by compound_id — matching on name would hit the legacy twin too"
        )

    def test_edges_are_moved_before_the_legacy_node_is_deleted(self):
        """Delete-before-move would destroy 218K edges."""
        driver, rec = _driver(_APPLY_RESPONSES())
        migrate(driver, "micromap", dry_run=False)
        cyphers = rec.cyphers()
        move_at = next(i for i, c in enumerate(cyphers) if "PRODUCES" in c and "MERGE" in c)
        del_at = next(i for i, c in enumerate(cyphers) if "DETACH DELETE" in c)
        assert move_at < del_at, "edges must be moved before the node is deleted"

    def test_move_preserves_edge_properties(self):
        driver, rec = _driver(_APPLY_RESPONSES())
        migrate(driver, "micromap", dry_run=False)
        move = next(c for c in rec.cyphers() if "MERGE" in c and "PRODUCES" in c)
        assert "evidence_level" in move, "curated evidence_level must survive the move"
        assert "notes" in move

    def test_move_is_batched(self):
        """acetate alone has 43,108 edges — one transaction would be reckless."""
        driver, rec = _driver(_APPLY_RESPONSES())
        migrate(driver, "micromap", dry_run=False)
        move = next(c for c in rec.cyphers() if "MERGE" in c and "PRODUCES" in c)
        assert "IN TRANSACTIONS" in move.upper(), "batch the edge move"

    def test_report_totals(self):
        driver, _ = _driver(_APPLY_RESPONSES())
        report = migrate(driver, "micromap", dry_run=False)
        assert report["dry_run"] is False
        assert report["total_edges_moved"] == 5553
        assert report["legacy_deleted"] == 1


class TestCliWiring:
    def _args(self, *argv):
        from database.load_knowledge_graph import _build_arg_parser
        return _build_arg_parser().parse_args(list(argv))

    def test_flags_registered(self):
        a = self._args("--migrate-curated-compounds", "--neo4j-database", "neo4j")
        assert a.migrate_curated_compounds is True
        assert a.apply is False

    def test_defaults_to_dry_run(self):
        from database.load_knowledge_graph import _dispatch
        from unittest.mock import patch
        args = self._args("--migrate-curated-compounds", "--neo4j-database", "neo4j")
        with patch("database.load_knowledge_graph.migrate_curated_compounds") as m:
            m.return_value = {"dry_run": True}
            _dispatch(args, MagicMock(), s3_manager=None)
        assert m.call_args.kwargs["dry_run"] is True, "must dry-run without --apply"

    def test_apply_performs_the_migration(self):
        from database.load_knowledge_graph import _dispatch
        from unittest.mock import patch
        args = self._args("--migrate-curated-compounds", "--apply",
                          "--neo4j-database", "neo4j")
        with patch("database.load_knowledge_graph.migrate_curated_compounds") as m:
            m.return_value = {"dry_run": False}
            _dispatch(args, MagicMock(), s3_manager=None)
        assert m.call_args.kwargs["dry_run"] is False

    def test_not_part_of_all(self):
        """Destructive: --all must never trigger it."""
        a = self._args("--all", "--neo4j-database", "neo4j")
        assert a.migrate_curated_compounds is False
