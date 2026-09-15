from pathlib import Path


from micromap_mapforge.inspect.sql_dump import inspect_sql_dump

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_inspect_sql_dump_basic():
    profile = inspect_sql_dump(FIXTURES / "study_dump.sql")
    assert profile.format == "sql_dump"
    assert profile.row_count_estimate == 3
    column_names = [c.name for c in profile.columns]
    assert column_names == ["tax_id", "organism", "condition", "log2fc", "pvalue"]


def test_inspect_sql_dump_type_inference():
    profile = inspect_sql_dump(FIXTURES / "study_dump.sql")
    by_name = {c.name: c for c in profile.columns}
    assert by_name["tax_id"].inferred_type == "integer"
    assert by_name["log2fc"].inferred_type == "float"
    assert by_name["organism"].inferred_type == "string"


def test_inspect_sql_dump_samples():
    profile = inspect_sql_dump(FIXTURES / "study_dump.sql")
    assert len(profile.samples) == 3
    assert profile.samples[0]["organism"] == "Escherichia coli"


def test_inspect_sql_dump_multiline_values(tmp_path: Path):
    sql = """CREATE TABLE `studies` (
  `tax_id` INT,
  `organism` VARCHAR(255),
  `note` VARCHAR(255)
);
INSERT INTO `studies` VALUES (
  562,
  'Escherichia coli',
  'long note'
);
"""
    dump = tmp_path / "multiline.sql"
    dump.write_text(sql, encoding="utf-8")
    profile = inspect_sql_dump(dump)
    assert profile.row_count_estimate == 1
    assert profile.samples[0]["organism"] == "Escherichia coli"
    assert profile.samples[0]["note"] == "long note"


def test_inspect_sql_dump_batch_insert(tmp_path: Path):
    """Real mysqldump output uses multi-row INSERT: VALUES (...), (...), (...);"""
    sql = """CREATE TABLE `studies` (
  `tax_id` INT,
  `organism` VARCHAR(255),
  `log2fc` FLOAT
);
INSERT INTO `studies` VALUES (562,'Escherichia coli',1.3),(1496,'Clostridium difficile',-2.1),(562,'Escherichia coli',0.8);
"""
    dump = tmp_path / "batch.sql"
    dump.write_text(sql, encoding="utf-8")
    profile = inspect_sql_dump(dump)
    assert profile.row_count_estimate == 3
    organisms = [row["organism"] for row in profile.samples]
    assert "Escherichia coli" in organisms
    assert "Clostridium difficile" in organisms


def test_inspect_sql_dump_batch_insert_mixed_quotes(tmp_path: Path):
    """Verify quote handling in batch form — ')' inside quoted strings shouldn't split a tuple."""
    sql = """CREATE TABLE `t` (
  `id` INT,
  `note` VARCHAR(255)
);
INSERT INTO `t` VALUES (1,'has ), inside quotes'),(2,'normal');
"""
    dump = tmp_path / "quoted.sql"
    dump.write_text(sql, encoding="utf-8")
    profile = inspect_sql_dump(dump)
    assert profile.row_count_estimate == 2
    notes = [row["note"] for row in profile.samples]
    assert "has ), inside quotes" in notes
    assert "normal" in notes


def test_inspect_sql_dump_multiline_batch(tmp_path: Path):
    """Batch INSERTs often span multiple lines in real dumps."""
    sql = """CREATE TABLE `t` (
  `id` INT,
  `name` VARCHAR(255)
);
INSERT INTO `t` VALUES
  (1, 'first'),
  (2, 'second'),
  (3, 'third');
"""
    dump = tmp_path / "multiline_batch.sql"
    dump.write_text(sql, encoding="utf-8")
    profile = inspect_sql_dump(dump)
    assert profile.row_count_estimate == 3
