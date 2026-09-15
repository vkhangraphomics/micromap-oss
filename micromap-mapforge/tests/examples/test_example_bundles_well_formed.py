"""Parametrized hygiene checks covering every shipped example bundle.

Replaces test_disbiome_bundle_well_formed.py — that file's six checks now
run against all five bundles automatically. Adding a new bundle under
examples/ means adding its name to ALL_BUNDLES; nothing else changes.

Runs in unit CI (no Neo4j required).
"""

import csv
from pathlib import Path

import pytest
import yaml

from micromap_mapforge.contributor.validator import (
    load_contributor,
    validate_contributor,
)
from micromap_mapforge.emit.routing_policy import load_routing_policy
from micromap_mapforge.mapping.validator import validate_mapping

REPO_ROOT = Path(__file__).resolve().parents[3]

ALL_BUNDLES = [
    "disbiome",
    "genomics-brca-mini",
    "transcriptomics-alzheimer-mini",
    "metabolomics-serum-mini",
    "proteomics-plasma-mini",
]


@pytest.fixture(params=ALL_BUNDLES)
def bundle_dir(request):
    return REPO_ROOT / "examples" / request.param


def test_bundle_directory_exists(bundle_dir):
    assert bundle_dir.is_dir(), f"missing bundle dir: {bundle_dir}"


def test_mapping_yaml_validates(bundle_dir):
    mapping_path = bundle_dir / "mapping.yaml"
    assert mapping_path.exists(), f"missing mapping.yaml in {bundle_dir.name}"
    mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    validate_mapping(mapping)


def test_contributor_yaml_validates(bundle_dir):
    contributor = load_contributor(bundle_dir / "contributor.yaml")
    validate_contributor(contributor)
    assert contributor["tier"] == "partner"
    assert contributor["sensitivity"] == "public"


def test_routing_policy_yaml_loads(bundle_dir):
    policy = load_routing_policy(bundle_dir / "routing-policy.yaml")
    assert policy is not None


def test_csv_columns_match_mapping_columns(bundle_dir):
    """Every CSV column the mapping references must exist in the data file."""
    mapping = yaml.safe_load(
        (bundle_dir / "mapping.yaml").read_text(encoding="utf-8")
    )
    csv_path = bundle_dir / mapping["source"]["path"]
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        csv_columns = set(reader.fieldnames or [])

    referenced: set[str] = set()
    for entity in mapping.get("entities", []):
        referenced.update(entity.get("columns", {}).values())
    for rel in mapping.get("relationships", []):
        for v in rel.get("properties", {}).values():
            if isinstance(v, str):
                referenced.add(v)

    missing = referenced - csv_columns
    assert not missing, (
        f"{bundle_dir.name}: mapping.yaml references CSV columns not in "
        f"{csv_path.name}: {sorted(missing)}\n"
        f"CSV columns: {sorted(csv_columns)}"
    )


def test_csv_has_enough_rows(bundle_dir):
    mapping = yaml.safe_load(
        (bundle_dir / "mapping.yaml").read_text(encoding="utf-8")
    )
    csv_path = bundle_dir / mapping["source"]["path"]
    with csv_path.open(encoding="utf-8") as f:
        row_count = sum(1 for _ in csv.DictReader(f))
    assert row_count >= 25, (
        f"{bundle_dir.name}: {csv_path.name} has only {row_count} rows; "
        f"expected ≥25 to exercise resolver + emitter meaningfully."
    )
