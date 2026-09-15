"""#273 follow-up: the taxonomy loader OOM-killed on kgdev.

The repair run died with `Killed` — `Memory cgroup out of memory: Killed process
(python)`. The `micromap-api` container has a hard **512 MB** limit, and the
current NCBI `nodes.dmp` has **2,895,864** taxa.

The loader read the whole taxonomy into Python before yielding anything:

    self._nodes[tax_id] = {"parent_tax_id": ..., "rank": ..., ...}

That is one inner dict per taxon — ~2.9M dict objects, several hundred MB on its
own — plus a full 2.9M-entry name map and, in the microbiome BFS, a
`children_map` dict-of-lists holding another entry per taxon.

String-keyed dicts cannot fit the budget at this scale whatever the tuning: 2.9M
entries is ~290 MB of dict overhead before values, plus ~160 MB of tax_id key
strings. Dense arrays indexed by integer tax_id are ~25 MB.

`dmesg` shows the same kill in March and June 2026, so this had been silently
killing loader runs for months.

These tests pin the behaviour that must survive the rewrite, plus the structural
property that caused the OOM.
"""
import pytest

from database.ingestion.ncbi_taxonomy_loader import NCBITaxonomyLoader


# tax_id | parent | rank | embl | division | ... | gencode | ...
NODES_DMP = """\
1\t|\t1\t|\tno rank\t|\t\t|\t8\t|\t0\t|\t1\t|\t0\t|
2\t|\t131567\t|\tsuperkingdom\t|\t\t|\t0\t|\t0\t|\t11\t|\t0\t|
131567\t|\t1\t|\tno rank\t|\t\t|\t8\t|\t0\t|\t1\t|\t0\t|
216851\t|\t216572\t|\tgenus\t|\t\t|\t0\t|\t0\t|\t11\t|\t0\t|
216572\t|\t2\t|\tfamily\t|\t\t|\t0\t|\t0\t|\t11\t|\t0\t|
853\t|\t216851\t|\tspecies\t|\t\t|\t0\t|\t0\t|\t11\t|\t0\t|
33090\t|\t131567\t|\tsuperkingdom\t|\t\t|\t1\t|\t0\t|\t1\t|\t0\t|
3702\t|\t33090\t|\tspecies\t|\t\t|\t1\t|\t0\t|\t1\t|\t0\t|
"""

NAMES_DMP = """\
1\t|\troot\t|\t\t|\tscientific name\t|
2\t|\tBacteria\t|\t\t|\tscientific name\t|
131567\t|\tcellular organisms\t|\t\t|\tscientific name\t|
216851\t|\tFaecalibacterium\t|\t\t|\tscientific name\t|
216572\t|\tOscillospiraceae\t|\t\t|\tscientific name\t|
853\t|\tFaecalibacterium prausnitzii\t|\t\t|\tscientific name\t|
853\t|\tF. prausnitzii\t|\t\t|\tcommon name\t|
33090\t|\tViridiplantae\t|\t\t|\tscientific name\t|
3702\t|\tArabidopsis thaliana\t|\t\t|\tscientific name\t|
"""


@pytest.fixture
def taxdump(tmp_path):
    (tmp_path / "nodes.dmp").write_text(NODES_DMP, encoding="utf-8")
    (tmp_path / "names.dmp").write_text(NAMES_DMP, encoding="utf-8")
    return tmp_path


def _loader(taxdump, filter_to_microbiome=True):
    ldr = NCBITaxonomyLoader.__new__(NCBITaxonomyLoader)
    ldr.taxdump_dir = str(taxdump)
    ldr.filter_to_microbiome = filter_to_microbiome
    ldr.organization_id = "default"
    ldr.batch_size = 100
    ldr.database = "neo4j"
    ldr._init_caches()
    return ldr


def _by_id(records):
    return {r["tax_id"]: r for r in records}


class TestExtractedRecordsAreUnchanged:
    """The rewrite must not alter what the loader produces."""

    def test_microbiome_subtree_is_included(self, taxdump):
        recs = _by_id(_loader(taxdump).extract())
        # Bacteria (2) and everything under it
        assert {"2", "216572", "216851", "853"} <= set(recs)

    def test_non_microbiome_subtree_is_excluded(self, taxdump):
        recs = _by_id(_loader(taxdump).extract())
        assert "3702" not in recs, "Arabidopsis is under Viridiplantae, not a microbe"
        assert "33090" not in recs

    def test_unfiltered_mode_yields_everything(self, taxdump):
        recs = _by_id(_loader(taxdump, filter_to_microbiome=False).extract())
        assert len(recs) == 8

    def test_parent_and_rank_survive(self, taxdump):
        rec = _by_id(_loader(taxdump).extract())["853"]
        assert rec["parent_tax_id"] == "216851"
        assert rec["rank"] == "species"

    def test_self_parent_is_normalised_to_none(self, taxdump):
        """Node 1's parent is itself in nodes.dmp; that must not become an edge."""
        rec = _by_id(_loader(taxdump, filter_to_microbiome=False).extract())["1"]
        assert rec["parent_tax_id"] is None

    def test_names_and_common_names_survive(self, taxdump):
        rec = _by_id(_loader(taxdump).extract())["853"]
        assert rec["name"] == "Faecalibacterium prausnitzii"
        assert rec["common_name"] == "F. prausnitzii"

    def test_division_and_genetic_code_survive(self, taxdump):
        rec = _by_id(_loader(taxdump).extract())["853"]
        assert rec["division_id"] == "0"
        assert rec["genetic_code_id"] == "11"

    def test_hierarchy_rollup_still_walks_ancestors(self, taxdump):
        """_build_hierarchy walks the parent chain — the property that lets an
        orphaned species still carry genus/family/phylum strings."""
        ldr = _loader(taxdump)
        list(ldr.extract())
        h = ldr._build_hierarchy("853")
        assert h["genus"] == "Faecalibacterium"
        assert h["family"] == "Oscillospiraceae"
        assert h["kingdom"] == "Bacteria"


class TestNoPerNodeDict:
    """The structural cause of the OOM (#273)."""

    def test_nodes_are_not_stored_as_a_dict_per_taxon(self, taxdump):
        ldr = _loader(taxdump)
        list(ldr.extract())
        offenders = [
            name for name, val in vars(ldr).items()
            if isinstance(val, dict) and val
            and all(isinstance(v, dict) for v in val.values())
        ]
        assert not offenders, (
            f"{offenders} stores a dict per taxon — 2.9M inner dicts is what "
            "OOM-killed the loader inside a 512 MB container (#273)"
        )

    def test_rank_strings_are_shared_not_duplicated_per_taxon(self, taxdump):
        """~40 distinct ranks across millions of taxa: they must not be a
        separate string object each."""
        ldr = _loader(taxdump, filter_to_microbiome=False)
        recs = list(ldr.extract())
        species = [r["rank"] for r in recs if r["rank"] == "species"]
        assert len(species) >= 2
        assert all(r is species[0] for r in species), (
            "identical rank strings are distinct objects — intern them"
        )
