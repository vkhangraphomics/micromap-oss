import json
import yaml
import pytest
from unittest.mock import patch, MagicMock
from micromap_mcp.mapforge_runner import MapForgeRunner, MapForgeError


def test_inspect_returns_dict_with_columns(tmp_path):
    tsv = tmp_path / "study.tsv"
    tsv.write_text("a\tb\n1\t2\n3\t4\n", encoding="utf-8")
    runner = MapForgeRunner()
    profile = runner.inspect(str(tsv))
    assert profile["format"] == "tsv"
    assert {c["name"] for c in profile["columns"]} == {"a", "b"}
    assert profile["row_count_estimate"] == 2


def test_inspect_threads_section_to_the_mztab_inspector(tmp_path):
    # #286 G4b via the runner: --section picks the protein table over the SML
    # auto-pick. Proves the runner forwards `section` to dispatch.inspect.
    mztab = tmp_path / "s.mztab"
    mztab.write_text(
        "MTD\tmzTab-version\t2.0.0-M\n"
        "SMH\tSML_ID\tdatabase_identifier\tchemical_name\n"
        "SML\t1\tCHEBI:16236\tethanol\n"
        "PRH\taccession\tdescription\n"
        "PRT\tP1\tAlpha\nPRT\tP2\tBeta\nPRT\tP3\tGamma\n",
        encoding="utf-8",
    )
    runner = MapForgeRunner()
    profile = runner.inspect(str(mztab), section="PRT")
    assert [c["name"] for c in profile["columns"]] == ["accession", "description"]
    assert profile["row_count_estimate"] == 3


def test_inspect_as_source_returns_a_raw_stub_with_meta(tmp_path):
    # #286 G8b via the runner: --as-source yields a provenance stub, and
    # _profile_to_dict must surface `meta` (size/sha256/read_count) — otherwise
    # the stub is useless over MCP.
    import hashlib
    payload = b"@r1\nACGT\n+\n!!!!\n@r2\nTTTT\n+\n####\n"  # 2 reads
    fq = tmp_path / "reads.fastq"
    fq.write_bytes(payload)
    runner = MapForgeRunner()
    profile = runner.inspect(str(fq), as_source=True)
    assert profile["format"] == "fastq"
    assert profile["columns"] == []
    assert profile["meta"]["read_count"] == 2
    assert profile["meta"]["sha256"] == hashlib.sha256(payload).hexdigest()


def test_inspect_omits_meta_for_a_plain_tabular_source(tmp_path):
    # meta is present only when an inspector sets it — a normal profile has none.
    tsv = tmp_path / "x.tsv"
    tsv.write_text("a\tb\n1\t2\n", encoding="utf-8")
    assert "meta" not in MapForgeRunner().inspect(str(tsv))


def test_draft_heuristic_returns_yaml_str(tmp_path):
    tsv = tmp_path / "study.tsv"
    tsv.write_text("ncbi_tax_id\torganism\n853\tF. prausnitzii\n", encoding="utf-8")
    runner = MapForgeRunner()
    yaml_str = runner.draft_heuristic(str(tsv))
    assert "entities:" in yaml_str
    assert "Taxon" in yaml_str


def test_draft_heuristic_attributes_inspect_failures_to_inspect_stage(tmp_path):
    """If _inspect fails inside draft_heuristic, stage must be 'inspect', not 'map'."""
    tsv = tmp_path / "study.tsv"
    tsv.write_text("a\tb\n1\t2\n", encoding="utf-8")
    with patch("micromap_mcp.mapforge_runner._inspect", side_effect=RuntimeError("inspect blew up")):
        with pytest.raises(MapForgeError) as exc:
            MapForgeRunner().draft_heuristic(str(tsv))
    assert exc.value.stage == "inspect"
    assert "inspect blew up" in exc.value.detail


def test_draft_heuristic_attributes_validate_failures_to_map_stage(tmp_path):
    """If validate_mapping fails, stage must be 'map'."""
    tsv = tmp_path / "study.tsv"
    tsv.write_text("ncbi_tax_id\torganism\n853\tF.p\n", encoding="utf-8")
    with patch("micromap_mcp.mapforge_runner.validate_mapping", side_effect=ValueError("schema invalid")):
        with pytest.raises(MapForgeError) as exc:
            MapForgeRunner().draft_heuristic(str(tsv))
    assert exc.value.stage == "map"
    assert "schema invalid" in exc.value.detail


def _seed_bundle(tmp_path):
    src = tmp_path / "src.tsv"
    src.write_text("ncbi_tax_id\torganism\tdisease\n853\tF.p\tcrohn disease\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    mapping = {
        "source": {"name": "t", "format": "tsv", "path": str(src)},
        "entities": [
            {"label": "Taxon", "match_on": "ncbi_tax_id",
             "columns": {"ncbi_tax_id": "ncbi_tax_id"}, "confidence": "EXTRACTED"},
            {"label": "Disease", "match_on": "name_normalized",
             "normalizer": "normalize_disease_name",
             "columns": {"name": "disease"}, "confidence": "EXTRACTED"},
        ],
        "relationships": [],
    }
    (bundle / "mapping.yaml").write_text(yaml.safe_dump(mapping), encoding="utf-8")
    return bundle


def test_resolve_writes_artifacts(tmp_path):
    bundle = _seed_bundle(tmp_path)
    runner = MapForgeRunner()
    with patch("micromap_mcp.mapforge_runner.build_resolvers") as br, \
         patch("micromap_mcp.mapforge_runner.GraphDatabase") as gdb:
        br.return_value = {}
        result = runner.resolve(
            str(bundle), neo4j_uri="bolt://x", neo4j_user="u",
            neo4j_password="p", neo4j_database="neo4j",
        )
        gdb.driver.assert_called_once()
    assert "resolved_count" in result
    assert (bundle / "resolution.json").exists()


def test_resolve_threads_organization_id_into_registry(tmp_path):
    """#314: the caller's org reaches build_resolvers so the preload is
    scoped; omitting it keeps the unscoped default (None)."""
    bundle = _seed_bundle(tmp_path)
    runner = MapForgeRunner()
    with patch("micromap_mcp.mapforge_runner.build_resolvers") as br, \
         patch("micromap_mcp.mapforge_runner.GraphDatabase"):
        br.return_value = {}
        runner.resolve(
            str(bundle), neo4j_uri="bolt://x", neo4j_user="u",
            neo4j_password="p", neo4j_database="neo4j",
            organization_id="org-a",
        )
        assert br.call_args.kwargs["organization_id"] == "org-a"


def test_plan_writes_routing(tmp_path):
    bundle = _seed_bundle(tmp_path)
    runner = MapForgeRunner()
    runner.plan(str(bundle), organization_id="default", destination="micromap-core")
    assert (bundle / "routing.yaml").exists()


def test_emit_generates_cypher(tmp_path):
    bundle = _seed_bundle(tmp_path)
    runner = MapForgeRunner()
    # Resolve first (mocked Neo4j) so resolution.json is written
    with patch("micromap_mcp.mapforge_runner.build_resolvers") as br, \
         patch("micromap_mcp.mapforge_runner.GraphDatabase"):
        br.return_value = {}
        runner.resolve(str(bundle), neo4j_uri="bolt://x", neo4j_user="u",
                       neo4j_password="p", neo4j_database="neo4j")
    runner.plan(str(bundle), organization_id="default", destination="micromap-core")
    runner.emit(str(bundle))
    cy = bundle / "cypher"
    assert cy.is_dir()


def test_approve_marks_manifest(tmp_path):
    bundle = _seed_bundle(tmp_path)
    (bundle / "manifest.json").write_text(json.dumps({}))
    runner = MapForgeRunner()
    out = runner.approve(str(bundle), reviewer="qa")
    assert out["reviewer"] == "qa"
    m = json.loads((bundle / "manifest.json").read_text())
    assert m["approved"] is True


def test_submit_invokes_executor_submit(tmp_path):
    bundle = _seed_bundle(tmp_path)
    (bundle / "cypher").mkdir()
    # Approval gate (added in C1 fix) requires manifest.json with approved=True.
    (bundle / "manifest.json").write_text(
        json.dumps({"approved": True, "reviewer": "qa"}), encoding="utf-8"
    )
    runner = MapForgeRunner()
    fake_exec = MagicMock()
    fake_exec.submit.return_value = MagicMock(
        success=True, nodes_written=10, relationships_written=5, contribution_id="c-1"
    )
    with patch("micromap_mcp.mapforge_runner.MicroMapCoreExecutor", return_value=fake_exec), \
         patch("micromap_mcp.mapforge_runner.GraphDatabase"):
        result = runner.submit(
            str(bundle), reviewer="qa",
            neo4j_uri="bolt://x", neo4j_user="u", neo4j_password="p", neo4j_database="neo4j",
        )
    assert result["nodes_written"] == 10
    assert result["rels_written"] == 5
    assert result["success"] is True


def test_emit_returns_cypher_files_and_bundle_dir(tmp_path):
    """Pin the spec-required return shape: {'cypher_files': [...], 'bundle_dir': str}."""
    from unittest.mock import patch
    bundle = _seed_bundle(tmp_path)
    runner = MapForgeRunner()
    with patch("micromap_mcp.mapforge_runner.build_resolvers") as br, \
         patch("micromap_mcp.mapforge_runner.GraphDatabase"):
        br.return_value = {}
        runner.resolve(str(bundle), neo4j_uri="bolt://x", neo4j_user="u",
                       neo4j_password="p", neo4j_database="neo4j")
    runner.plan(str(bundle), organization_id="default", destination="micromap-core")
    result = runner.emit(str(bundle))
    assert set(result.keys()) == {"cypher_files", "bundle_dir"}
    assert isinstance(result["cypher_files"], list)
    assert result["bundle_dir"] == str(bundle)


# ---------------------------------------------------------------------------
# MCP gap-fill: templates discovery + schema_config-aware mapping
# ---------------------------------------------------------------------------


def test_templates_list_returns_three_entries_with_metadata():
    """runner.templates_list() returns sorted dicts with name/version/description.

    At minimum the three shipped templates (microbiome, genomics,
    transcriptomics) must be present.
    """
    runner = MapForgeRunner()
    entries = runner.templates_list()
    assert isinstance(entries, list)
    assert len(entries) >= 3

    by_name = {e["name"]: e for e in entries}
    assert {"microbiome", "genomics", "transcriptomics"} <= set(by_name.keys())

    for entry in entries:
        assert entry["name"] and isinstance(entry["name"], str)
        assert entry["version"] and isinstance(entry["version"], str)
        assert entry["description"] and isinstance(entry["description"], str)

    # Sorted alphabetically by name.
    names = [e["name"] for e in entries]
    assert names == sorted(names)


def test_templates_show_returns_canonical_name_and_yaml():
    """runner.templates_show(<name>) returns canonical name + raw YAML."""
    runner = MapForgeRunner()
    result = runner.templates_show("genomics")
    assert result["name"] == "genomics"
    yaml_content = result["yaml_content"]
    assert isinstance(yaml_content, str)
    # Sanity-check that this is actually the genomics template content
    # without pinning the exact spelling of the top-level `name:` value
    # (which is a curation choice that could legitimately change).
    assert "genomics" in yaml_content
    # Raw file preserved (comments survive).
    assert "#" in yaml_content


def test_templates_show_is_case_insensitive():
    """templates_show normalizes the input name; all three case variants
    produce the same canonical 'genomics'."""
    runner = MapForgeRunner()
    for variant in ("GENOMICS", "Genomics", "genomics"):
        result = runner.templates_show(variant)
        assert result["name"] == "genomics"


def test_templates_show_unknown_name_raises_mapforge_error():
    """An unknown template name raises MapForgeError(stage='templates') with
    the available templates listed in the error detail.

    Pins the agent-facing contract: the error includes both the offending
    input AND the alternatives, so the agent can self-correct or relay an
    actionable message to the user.
    """
    runner = MapForgeRunner()
    with pytest.raises(MapForgeError) as exc:
        runner.templates_show("not-a-template")
    assert exc.value.stage == "templates"
    detail = exc.value.detail
    assert "not-a-template" in detail
    # At least one available template name must appear so the agent has an
    # actionable alternative.
    assert any(name in detail for name in ("genomics", "microbiome", "transcriptomics"))


def test_draft_heuristic_with_schema_config_uses_template_and_seals_sha(tmp_path):
    """draft_heuristic with schema_config='genomics' produces genomics-ontology
    entities AND seals source_sha256 (E5 alignment).

    The two assertions live in the same test because both contracts are
    verified by parsing the same returned YAML; splitting would duplicate
    the seed work.
    """
    import hashlib
    csv = tmp_path / "data.csv"
    csv.write_text(
        "gene_symbol,clinvar_id\nBRCA1,RCV000077444\n", encoding="utf-8",
    )
    expected_sha = hashlib.sha256(csv.read_bytes()).hexdigest()

    runner = MapForgeRunner()
    yaml_str = runner.draft_heuristic(str(csv), schema_config="genomics")
    mapping = yaml.safe_load(yaml_str)

    # Genomics-ontology recognition: entities include Gene + Variant.
    entity_labels = {e["label"] for e in mapping.get("entities", [])}
    assert "Gene" in entity_labels, (
        f"genomics template not applied; labels: {entity_labels}"
    )
    assert "Variant" in entity_labels, (
        f"genomics template not applied; labels: {entity_labels}"
    )

    # E5 alignment: source.sha256 sealed into mapping.yaml.
    sealed_sha = mapping.get("source", {}).get("sha256")
    assert sealed_sha == expected_sha, (
        f"source.sha256 not sealed correctly; got {sealed_sha!r}, "
        f"expected {expected_sha!r}"
    )


def _write_10x(d):
    """A minimal 10x matrix directory (features/barcodes/matrix triplet)."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "features.tsv").write_text(
        "ENSG1\tTP53\tGene Expression\nENSG2\tBRCA1\tGene Expression\n",
        encoding="utf-8",
    )
    (d / "barcodes.tsv").write_text("BC1-1\nBC2-1\n", encoding="utf-8")
    (d / "matrix.mtx").write_text(
        "%%MatrixMarket matrix coordinate integer general\n2 2 2\n1 1 5\n2 2 7\n",
        encoding="utf-8",
    )
    return d


def test_draft_heuristic_seals_sha_for_a_directory_source(tmp_path):
    """A directory source (10x matrix, #286 G3b) has no single file to hash.
    draft_heuristic must still succeed and seal a deterministic manifest digest
    — not crash with IsADirectoryError from read_bytes() on the directory.
    """
    import hashlib
    d = _write_10x(tmp_path / "filtered_feature_bc_matrix")

    # Expected: sha256 over a sorted (relpath\0filesha\n) manifest of the dir.
    h = hashlib.sha256()
    for f in sorted(p for p in d.rglob("*") if p.is_file()):
        rel = f.relative_to(d).as_posix()
        h.update(f"{rel}\0{hashlib.sha256(f.read_bytes()).hexdigest()}\n".encode())
    expected_sha = h.hexdigest()

    runner = MapForgeRunner()
    yaml_str = runner.draft_heuristic(str(d), schema_config="transcriptomics")
    mapping = yaml.safe_load(yaml_str)

    assert mapping["source"]["format"] == "10x"
    assert "Gene" in {e["label"] for e in mapping.get("entities", [])}
    sealed = mapping.get("source", {}).get("sha256")
    assert sealed == expected_sha, (
        f"directory source sha not sealed as a manifest digest; "
        f"got {sealed!r}, expected {expected_sha!r}"
    )


def test_source_sha256_is_sensitive_to_directory_contents(tmp_path):
    """The directory manifest digest must change when a file's contents change,
    so a moved/edited 10x source cannot silently reuse a stale sealed sha."""
    from micromap_mcp.mapforge_runner import _source_sha256
    d = _write_10x(tmp_path / "run")
    before = _source_sha256(d)
    (d / "features.tsv").write_text(
        "ENSG1\tTP53\tGene Expression\nENSG9\tKRAS\tGene Expression\n",
        encoding="utf-8",
    )
    assert _source_sha256(d) != before


def test_draft_heuristic_unknown_schema_config_name_raises_mapforge_error(tmp_path):
    """An unknown schema_config name raises MapForgeError(stage='map') with
    the alternatives list preserved in the detail.

    Pins that SchemaConfigError's detailed message (which includes the
    available built-in names) survives the `except Exception → MapForgeError`
    conversion intact.
    """
    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    runner = MapForgeRunner()
    with pytest.raises(MapForgeError) as exc:
        runner.draft_heuristic(str(csv), schema_config="not-a-real-discipline")
    assert exc.value.stage == "map"
    detail = exc.value.detail
    assert "not-a-real-discipline" in detail
    # At least one available built-in surfaces so the agent can self-correct.
    assert any(name in detail for name in ("genomics", "microbiome", "transcriptomics"))
