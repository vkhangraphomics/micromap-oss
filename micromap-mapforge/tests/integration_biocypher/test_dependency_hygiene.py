"""Static checks that lock in the architectural commitment to BioCypher core only (R1).

A previous `test_pip_check_is_clean` test that ran `pip check` via
subprocess was removed (review finding #12): CI runs `pip check` as its
own dedicated step in .github/workflows/ci.yml ("Dependency hygiene"),
and the pytest copy was both duplicative and PATH-fragile — on pyenv /
conda / virtualenv setups, `pip` on PATH might bind to a different
interpreter than the active pytest one, producing the wrong signal.
"""

import re
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_pyproject_declares_biocypher_core():
    text = PYPROJECT.read_text(encoding="utf-8")
    assert re.search(r'"biocypher>=0\.15\.1"', text), \
        "Expected biocypher>=0.15.1 in dependencies (core only)"


def test_pyproject_does_not_declare_biocypher_neo4j_extra():
    text = PYPROJECT.read_text(encoding="utf-8")
    # Never depend on biocypher[neo4j]; it pulls a peer neo4j>=5.0 that
    # conflicts with our neo4j>=5.15 bound (§7 R1) and re-couples the producer
    # to a peer driver, which we deliberately don't want.
    assert "biocypher[neo4j]" not in text, \
        "MapForge must NEVER depend on biocypher[neo4j]; offline producer only"
