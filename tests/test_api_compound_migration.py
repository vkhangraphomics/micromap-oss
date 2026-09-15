"""Regression: the API read-side must query :Compound, not :Metabolite (#146 / GPV-403).

The #146 migration moved every loader from the :Metabolite label to :Compound,
but the API query layer (stats + routes) was left matching :Metabolite — a label
nothing writes anymore — so /api/v1/stats reported metabolites=0 and the
metabolite endpoints returned nothing even when compounds were loaded.

This guard inspects the source of api/main.py and every api/routes/*.py module
and fails if any of them contains the ':Metabolite' label token. The canonical
label is ':Compound'. (The bare response-type string 'Metabolite' — e.g.
RETURN 'Metabolite' AS type — is intentionally allowed; only the label token,
which is always written with a leading colon, is forbidden.)
"""
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent.parent / "api"

API_FILES = [API_DIR / "main.py", *sorted((API_DIR / "routes").glob("*.py"))]


@pytest.mark.parametrize("py_file", API_FILES, ids=lambda p: p.name)
def test_api_module_has_no_metabolite_label(py_file):
    source = py_file.read_text(encoding="utf-8")
    assert ":Metabolite" not in source, (
        f"{py_file.name} still queries the ':Metabolite' label — "
        "the canonical label is ':Compound' since #146 (GPV-403). "
        "Loaders write :Compound; matching :Metabolite returns nothing."
    )
