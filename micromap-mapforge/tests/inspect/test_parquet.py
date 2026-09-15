# micromap-mapforge/tests/inspect/test_parquet.py
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from micromap_mapforge.inspect.parquet import inspect_parquet

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def study_parquet(tmp_path: Path) -> Path:
    table = pa.table(
        {
            "tax_id": [562, 1496, 562],
            "organism": ["Escherichia coli", "Clostridium difficile", "Escherichia coli"],
            "condition": ["Crohn's Disease", "Ulcerative Colitis", "Ulcerative Colitis"],
            "log2fc": [1.3, -2.1, 0.8],
            "pvalue": [0.001, 0.0003, 0.02],
        }
    )
    out = tmp_path / "study.parquet"
    pq.write_table(table, out)
    return out


def test_inspect_parquet_basic(study_parquet: Path):
    profile = inspect_parquet(study_parquet)
    assert profile.format == "parquet"
    assert profile.row_count_estimate == 3
    column_names = [c.name for c in profile.columns]
    assert column_names == ["tax_id", "organism", "condition", "log2fc", "pvalue"]


def test_inspect_parquet_type_inference(study_parquet: Path):
    profile = inspect_parquet(study_parquet)
    by_name = {c.name: c for c in profile.columns}
    assert by_name["tax_id"].inferred_type == "integer"
    assert by_name["log2fc"].inferred_type == "float"
    assert by_name["organism"].inferred_type == "string"


def test_inspect_parquet_samples(study_parquet: Path):
    profile = inspect_parquet(study_parquet)
    assert len(profile.samples) == 3
    assert profile.samples[0]["organism"] == "Escherichia coli"
