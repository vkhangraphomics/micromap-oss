"""C3a (#76): real-network smoke. Skipped by default; run with -m network.

Hits a tiny public Zenodo record end to end. Not in CI (and CI billing is down).
"""
import pytest

from micromap_mapforge.fetch.zenodo import fetch_zenodo

pytestmark = pytest.mark.network


def test_real_zenodo_fetch(tmp_path):
    # A small, stable public Zenodo record id. Replace if it ever 404s.
    fr = fetch_zenodo("10047808", dest_dir=tmp_path)
    assert fr.local_path.exists()
    assert len(fr.sha256) == 64
    assert fr.doi.startswith("10.5281/zenodo.")


from micromap_mapforge.fetch.figshare import fetch_figshare
from micromap_mapforge.fetch.dryad import fetch_dryad
from micromap_mapforge.fetch.osf import fetch_osf


def test_real_figshare_fetch(tmp_path):
    # Replace the id if it 404s; any small public Figshare article works.
    fr = fetch_figshare("1234567", dest_dir=tmp_path)
    assert fr.local_path.exists() and len(fr.sha256) == 64


def test_real_dryad_fetch(tmp_path):
    fr = fetch_dryad("10.5061/dryad.0k6djhb7v", dest_dir=tmp_path)
    assert fr.local_path.exists() and len(fr.sha256) == 64


def test_real_osf_fetch(tmp_path):
    fr = fetch_osf("9zpcy", dest_dir=tmp_path)
    assert fr.local_path.exists() and len(fr.sha256) == 64
