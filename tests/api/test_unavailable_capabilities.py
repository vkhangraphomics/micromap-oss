"""Endpoints whose data does not exist must refuse, not answer empty (#305).

Three surfaces queried relationship types (and one node label) that no loader
writes, so they returned `200` with an empty result for every request:

- `/networks/cross-feeding*` matched `CROSS_FEEDS`. Taxon->Taxon holds only
  `HAS_PARENT` (811,720 edges) -- there is no cross-feeding edge of any kind.
  These are plain `MATCH`, not `OPTIONAL`, so the endpoints returned zero rows.
- `/biomarkers*` matched `PREDICTS` / `INCLUDES` on `:BiomarkerSignature`, a
  node label absent from `db.labels()` entirely. The whole surface is routed
  but was never ingested.

An empty `200` from these reads as a finding -- "this taxon has no cross-feeding
partners", "this disease has no biomarker signatures" -- which is a claim about
the biology. The truth is that we hold no such data. Same reasoning as #304,
where `/genes/{id}/taxa` was changed to 501 for the identical reason.

`AFFECTS_TAXON` in `drugs.py` is the opposite case and is repaired rather than
refused: `EFFECTIVE_AGAINST` is the ONLY Drug<->Taxon edge in the graph
(294,275 of them) and `/drugs/{id}/taxa` already surfaces it through an untyped
match. The name was simply wrong, so those endpoints now return real data.
"""
import ast
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

_ROUTES = Path(__file__).resolve().parent.parent.parent / "api" / "routes"


def _client(module_name: str) -> TestClient:
    from importlib import import_module

    module = import_module(f"api.routes.{module_name}")
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def _query_text(source: str) -> str:
    """Executable string literals only (see test_phantom_relationship_types)."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return "\n".join(
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and id(n) not in docstrings
    )


class TestCrossFeedingRefuses:
    """No cross-feeding edge exists, so the network cannot be reported."""

    @pytest.mark.parametrize("path", [
        "/api/v1/networks/cross-feeding",
        "/api/v1/networks/cross-feeding/NCBITaxon:1654",
    ])
    def test_returns_501_not_an_empty_network(self, path):
        response = _client("networks").get(path)

        assert response.status_code == 501, (
            f"{path} returned {response.status_code}; an empty network reads as "
            f"'this taxon has no cross-feeding partners', which is a claim we "
            f"cannot support (#305)"
        )
        assert "305" in response.json()["detail"]

    def test_cross_feeds_is_no_longer_queried(self):
        source = _query_text((_ROUTES / "networks.py").read_text(encoding="utf-8"))

        assert "CROSS_FEEDS" not in source


class TestBiomarkersRefuse:
    """`:BiomarkerSignature` is absent from db.labels(); nothing to serve."""

    @pytest.mark.parametrize("path", [
        "/api/v1/biomarkers",
        "/api/v1/biomarkers/disease/crohn-disease",
        "/api/v1/biomarkers/sig-1",
    ])
    def test_returns_501_not_an_empty_list(self, path):
        response = _client("biomarkers").get(path)

        assert response.status_code == 501, (
            f"{path} returned {response.status_code}; an empty result reads as "
            f"'no signature exists', but the label itself is not in the graph "
            f"(#305)"
        )
        assert "305" in response.json()["detail"]

    def test_phantom_types_and_label_are_no_longer_queried(self):
        """Cypher patterns, not bare substrings.

        The 501 message names PREDICTS/INCLUDES/:BiomarkerSignature on purpose,
        so the caller learns what is missing. Forbidding the words would forbid
        explaining the refusal; what must be gone is the graph pattern.
        """
        source = _query_text((_ROUTES / "biomarkers.py").read_text(encoding="utf-8"))

        for rel_type in ("PREDICTS", "INCLUDES"):
            assert not re.search(rf"-\[\s*\w*\s*:\s*{rel_type}\s*[\]{{]", source), (
                f"{rel_type} is still matched in Cypher; it does not exist"
            )
        assert not re.search(r"\(\s*\w*\s*:\s*BiomarkerSignature", source), (
            "a Cypher node pattern still binds :BiomarkerSignature, a label "
            "absent from db.labels()"
        )


class TestDrugTaxonEdgeIsRepaired:
    """AFFECTS_TAXON was the wrong name for an edge that does exist."""

    def test_drugs_query_the_real_edge(self):
        source = _query_text((_ROUTES / "drugs.py").read_text(encoding="utf-8"))

        assert "AFFECTS_TAXON" not in source, (
            "AFFECTS_TAXON is written by no loader and absent from the graph"
        )
        assert "EFFECTIVE_AGAINST" in source, (
            "EFFECTIVE_AGAINST is the only Drug<->Taxon edge (294,275 edges) "
            "and is what these endpoints meant all along"
        )

    def test_no_direction_property_is_read_from_that_edge(self):
        """`EFFECTIVE_AGAINST` carries mechanism/source/created_at -- no
        `direction`. Reading `r1.direction` would silently yield null for every
        row, swapping one invisible emptiness for another."""
        source = _query_text((_ROUTES / "drugs.py").read_text(encoding="utf-8"))

        assert not re.search(r"r1\.direction", source), (
            "EFFECTIVE_AGAINST has no `direction` property; use `mechanism`"
        )
