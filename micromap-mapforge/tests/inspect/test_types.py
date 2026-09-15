from micromap_mapforge.inspect.types import SourceProfile, ColumnProfile


def test_column_profile_to_dict():
    col = ColumnProfile(
        name="tax_id",
        inferred_type="integer",
        null_rate=0.0,
        distinct_count=42,
        samples=[123, 456, 789],
    )
    d = col.to_dict()
    assert d["name"] == "tax_id"
    assert d["inferred_type"] == "integer"
    assert d["null_rate"] == 0.0
    assert d["distinct_count"] == 42
    assert d["samples"] == [123, 456, 789]


def test_source_profile_to_dict_roundtrip():
    profile = SourceProfile(
        path="/tmp/study.tsv",
        format="tsv",
        row_count_estimate=1000,
        columns=[
            ColumnProfile(name="tax_id", inferred_type="integer", null_rate=0.0,
                          distinct_count=42, samples=[1, 2, 3]),
        ],
        samples=[{"tax_id": 1}, {"tax_id": 2}],
    )
    d = profile.to_dict()
    assert d["path"] == "/tmp/study.tsv"
    assert d["format"] == "tsv"
    assert d["row_count_estimate"] == 1000
    assert len(d["columns"]) == 1
    assert d["columns"][0]["name"] == "tax_id"
    assert d["samples"] == [{"tax_id": 1}, {"tax_id": 2}]
