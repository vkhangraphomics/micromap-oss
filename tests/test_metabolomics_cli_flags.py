"""Tests for metabolomics CLI flags in load_knowledge_graph.py.

Covers:
  --metabolomics-hmdb  → HMDBMetabolomicsLoader
  --metacyc            → MetaCycLoader
  --metabo-lights      → MetaboLightsLoader
  --metabolomics       → all three in order
"""

import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


# Ensure the project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(**kwargs) -> argparse.Namespace:
    """Return a Namespace whose flags all default to False/None."""
    defaults = dict(
        all=False,
        indexes=False,
        taxonomy=False,
        disbiome=False,
        produces=False,
        neurological=False,
        hmdb=False,
        kegg=False,
        chembl=False,
        pubchem=False,
        gutmdisorder=False,
        gmrepo=False,
        bugsigdb=False,
        pubmed=False,
        semmeddb=False,
        mbodymap=False,
        reactome=False,
        metabolomics_hmdb=False,
        metacyc=False,
        metabo_lights=False,
        metabolomics=False,
        uniprot_proteomics=False,
        phosphosite=False,
        pride=False,
        proteomics=False,
        validate=False,
        validate_xrefs=False,
        deduplicate=False,
        derive=False,
        backfill_scfa=False,
        migrate_curated_compounds=False,
        backfill_disbiome_papers=False,
        migrate_duplicate_diseases=False,
        apply=False,
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="password",
        neo4j_database="neo4j",
        org_id="default",
        s3_bucket="",
        s3_prefix="data/",
        upload_data=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# Import the orchestrator functions under test
# ---------------------------------------------------------------------------

from database.load_knowledge_graph import (
    load_hmdb_metabolomics_data,
    load_metacyc_data,
    load_metabo_lights_data,
)


# ---------------------------------------------------------------------------
# Unit tests: each loader function instantiates the right class
# ---------------------------------------------------------------------------

class TestLoadHmdbMetabolomicsData:
    def test_returns_none_when_data_not_found(self, tmp_path):
        """load_hmdb_metabolomics_data returns None when no data file is present."""
        driver = MagicMock()
        # Point at an empty directory so no candidate file is found
        with patch.dict("os.environ", {"HMDB_METABOLOMICS_DATA_PATH": str(tmp_path)}):
            result = load_hmdb_metabolomics_data(driver, organization_id="test-org", database="neo4j")
        assert result is None


class TestLoadMetacycData:
    def test_returns_none_when_data_not_found(self, tmp_path):
        """load_metacyc_data returns None when the data directory is missing."""
        driver = MagicMock()
        missing = str(tmp_path / "metacyc_does_not_exist")
        with patch.dict("os.environ", {"METACYC_DATA_PATH": missing}):
            result = load_metacyc_data(driver, organization_id="test-org", database="neo4j")
        assert result is None


class TestLoadMetaboLightsData:
    def test_returns_none_when_data_not_found(self, tmp_path):
        """load_metabo_lights_data returns None when the data path doesn't exist."""
        driver = MagicMock()
        missing = str(tmp_path / "metabo_lights_does_not_exist")
        with patch.dict("os.environ", {"METABO_LIGHTS_DATA_PATH": missing}):
            result = load_metabo_lights_data(driver, organization_id="test-org", database="neo4j")
        assert result is None


# ---------------------------------------------------------------------------
# CLI flag parsing tests — confirm new flags exist in the argument parser
# ---------------------------------------------------------------------------

class TestArgParsing:
    """Parse CLI strings to verify the new flags are registered."""

    def _parse(self, *argv):
        # Re-create the parser in the same way main() does
        from database.load_knowledge_graph import _build_arg_parser
        parser = _build_arg_parser()
        return parser.parse_args(list(argv))

    def test_metabolomics_hmdb_flag_is_registered(self):
        args = self._parse("--metabolomics-hmdb")
        assert args.metabolomics_hmdb is True

    def test_metacyc_flag_is_registered(self):
        args = self._parse("--metacyc")
        assert args.metacyc is True

    def test_metabo_lights_flag_is_registered(self):
        args = self._parse("--metabo-lights")
        assert args.metabo_lights is True

    def test_metabolomics_convenience_flag_is_registered(self):
        args = self._parse("--metabolomics")
        assert args.metabolomics is True


# ---------------------------------------------------------------------------
# Dispatch tests — confirm the main() loop calls the right functions
# ---------------------------------------------------------------------------

class TestDispatch:
    """Verify that each flag triggers the correct loader function."""

    # Names of loader functions to stub out (keeps other tests from hitting Neo4j)
    _OTHER_LOADERS = [
        "create_indexes_and_constraints",
        "load_ncbi_taxonomy",
        "create_sample_disbiome_data",
        "load_disbiome_data",
        "load_produces_relationships",
        "load_neurological_data",
        "load_hmdb_data",
        "load_kegg_data",
        "load_chembl_data",
        "load_pubchem_data",
        "load_gutmdisorder_data",
        "load_gmrepo_data",
        "load_bugsigdb_data",
        "load_pubmed_data",
        "load_semmeddb_data",
        "load_mbodymap_data",
        "load_reactome_data",
        "load_uniprot_proteomics_data",
        "load_phosphosite_data",
        "load_pride_data",
    ]

    def _run_main_with_args(self, args: argparse.Namespace):
        """Patch all loader functions, then call _dispatch; return the 3 mocks."""
        from contextlib import ExitStack
        from database.load_knowledge_graph import _dispatch

        mock_driver = MagicMock()
        base = "database.load_knowledge_graph."

        with ExitStack() as stack:
            # Patch the three new loaders and capture their mocks
            p_hmdb_m = stack.enter_context(patch(base + "load_hmdb_metabolomics_data"))
            p_metacyc = stack.enter_context(patch(base + "load_metacyc_data"))
            p_metabo = stack.enter_context(patch(base + "load_metabo_lights_data"))

            # Patch taxonomy download so it doesn't hit the network
            stack.enter_context(
                patch(base + "download_ncbi_taxonomy", return_value=Path("/tmp/taxdump"))
            )

            # Stub every other loader function
            for fn in self._OTHER_LOADERS:
                stack.enter_context(patch(base + fn))

            _dispatch(args, mock_driver, s3_manager=None)

        return p_hmdb_m, p_metacyc, p_metabo

    def test_metabolomics_hmdb_flag_runs_hmdb_metabolomics_loader(self):
        args = _make_args(metabolomics_hmdb=True)
        p_hmdb_m, p_metacyc, p_metabo = self._run_main_with_args(args)
        p_hmdb_m.assert_called_once()
        p_metacyc.assert_not_called()
        p_metabo.assert_not_called()

    def test_metacyc_flag_runs_metacyc_loader(self):
        args = _make_args(metacyc=True)
        p_hmdb_m, p_metacyc, p_metabo = self._run_main_with_args(args)
        p_hmdb_m.assert_not_called()
        p_metacyc.assert_called_once()
        p_metabo.assert_not_called()

    def test_metabo_lights_flag_runs_metabo_lights_loader(self):
        args = _make_args(metabo_lights=True)
        p_hmdb_m, p_metacyc, p_metabo = self._run_main_with_args(args)
        p_hmdb_m.assert_not_called()
        p_metacyc.assert_not_called()
        p_metabo.assert_called_once()

    def test_metabolomics_convenience_flag_runs_all_three(self):
        args = _make_args(metabolomics=True)
        p_hmdb_m, p_metacyc, p_metabo = self._run_main_with_args(args)
        p_hmdb_m.assert_called_once()
        p_metacyc.assert_called_once()
        p_metabo.assert_called_once()
