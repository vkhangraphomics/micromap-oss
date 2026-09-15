"""Runner: load a BioCypher adapter offline → IR. No live Neo4j."""

from pathlib import Path

import pytest

from micromap_mapforge.confidence import Confidence
from micromap_mapforge.integration.biocypher.ir import (
    ContributionBundle,
    SourceRef,
    validate_bundle_dict,
)
from micromap_mapforge.integration.biocypher.runner import (
    AdapterLoadError,
    OfflineAdapter,
    build_contribution_bundle,
    load_adapter,
    run_adapter,
)


# A minimal in-process adapter class used as a fixture across runner tests.
class FakeGtdbAdapter:
    """Adapter API contract: get_nodes() / get_edges() / schema_config / name."""

    name = "fake-gtdb"
    schema_config = {"name": "biolink-mini", "prefixes": {"NCBITaxon": "http://x/"}}

    def get_nodes(self):
        yield ("NCBITaxon:9606", "OrganismTaxon",
               {"name": "Homo sapiens"},
               {"source": "gtdb", "method": "curated"},
               "EXTRACTED",
               None)  # tier

    def get_edges(self):
        yield ("MEMBER_OF", "NCBITaxon:9606", "NCBITaxon:9605",
               {"evidence": "ncbi"},
               {"source": "gtdb", "method": "curated"},
               "EXTRACTED",
               None)


def test_load_adapter_resolves_dotted_path():
    cls = load_adapter("tests.integration_biocypher.test_runner:FakeGtdbAdapter")
    assert cls is FakeGtdbAdapter


def test_load_adapter_raises_on_unknown_path():
    with pytest.raises(AdapterLoadError, match="cannot import"):
        load_adapter("nonexistent.module:Whatever")


def test_load_adapter_raises_on_missing_attribute():
    with pytest.raises(AdapterLoadError, match="has no attribute"):
        load_adapter("tests.integration_biocypher.test_runner:NoSuchClass")


def test_run_adapter_returns_offlineadapter_with_schema_config():
    adapter = FakeGtdbAdapter()
    offline: OfflineAdapter = run_adapter(adapter)
    assert offline.schema_config["name"] == "biolink-mini"
    assert offline.name == "fake-gtdb"
    # Drainage check: run_adapter must materialize get_nodes() / get_edges()
    # into concrete lists, not leave the generators unconsumed.
    assert len(offline.nodes) == 1
    assert len(offline.edges) == 1
    assert offline.nodes[0][0] == "NCBITaxon:9606"   # tuple position 0 = id
    assert offline.edges[0][0] == "MEMBER_OF"        # tuple position 0 = type


def test_build_contribution_bundle_attaches_provenance_and_confidence(tmp_path: Path):
    source_file = tmp_path / "gtdb.tsv"
    source_file.write_text("dummy", encoding="utf-8")
    adapter = FakeGtdbAdapter()
    offline = run_adapter(adapter)

    bundle: ContributionBundle = build_contribution_bundle(
        offline,
        organization_id="org-test",
        source=SourceRef(
            kind="file",
            path=str(source_file),
            sha256="b"*64,
        ),
        schema_version="1.0",
    )

    assert bundle.organization_id == "org-test"
    assert bundle.schema_config["name"] == "biolink-mini"
    assert len(bundle.nodes) == 1
    node = bundle.nodes[0]
    assert node.label == "OrganismTaxon"
    assert node.confidence == Confidence.EXTRACTED
    assert node.provenance == {"source": "gtdb", "method": "curated"}

    assert len(bundle.edges) == 1
    edge = bundle.edges[0]
    assert edge.type == "MEMBER_OF"
    assert edge.confidence == Confidence.EXTRACTED


def test_build_contribution_bundle_passes_schema_validation(tmp_path: Path):
    source_file = tmp_path / "gtdb.tsv"
    source_file.write_text("dummy", encoding="utf-8")
    offline = run_adapter(FakeGtdbAdapter())
    bundle = build_contribution_bundle(
        offline,
        organization_id="org-test",
        source=SourceRef(kind="file", path=str(source_file), sha256="b"*64),
        schema_version="1.0",
    )
    # PR #101 Important #5: cleaning ritual extracted into bundle_to_schema_dict.
    from micromap_mapforge.integration.biocypher.ir import bundle_to_schema_dict
    validate_bundle_dict(bundle_to_schema_dict(bundle))


from micromap_mapforge.integration.biocypher.runner import source_ref_for_path


def test_source_ref_for_path_returns_file_kind_for_a_file(tmp_path: Path):
    p = tmp_path / "a.tsv"
    p.write_text("hello", encoding="utf-8")
    ref = source_ref_for_path(p)
    assert ref.kind == "file"
    assert ref.path == str(p)
    assert ref.archive_path is None
    # sha256 of "hello"
    import hashlib
    assert ref.sha256 == hashlib.sha256(b"hello").hexdigest()


def test_source_ref_for_path_returns_dir_kind_for_a_directory(tmp_path: Path):
    d = tmp_path / "dir"
    d.mkdir()
    (d / "a.tsv").write_text("a", encoding="utf-8")
    (d / "b.tsv").write_text("b", encoding="utf-8")
    ref = source_ref_for_path(d)
    assert ref.kind == "dir"
    assert ref.path == str(d)
    # tree sha256: file paths concatenated with their hashes in sorted order.
    import hashlib
    h = hashlib.sha256()
    for name in ("a.tsv", "b.tsv"):
        h.update(name.encode("utf-8"))
        h.update(b":")
        h.update(hashlib.sha256((d / name).read_bytes()).hexdigest().encode("utf-8"))
        h.update(b"\n")
    assert ref.sha256 == h.hexdigest()


def test_source_ref_for_path_returns_archive_kind_for_tar_gz(tmp_path: Path):
    arc = tmp_path / "thing.tar.gz"
    arc.write_bytes(b"\x1f\x8b\x08\x00")  # gzip magic; treat as opaque blob
    ref = source_ref_for_path(arc)
    assert ref.kind == "archive"
    assert ref.archive_path == str(arc)
    assert ref.path is None


def test_run_adapter_to_bundle_dotted_path_e2e(tmp_path: Path):
    """End-to-end at the unit level: dotted path → loaded class → IR → serialized bundle."""
    source_file = tmp_path / "fake.tsv"
    source_file.write_text("ignored", encoding="utf-8")
    cls = load_adapter("tests.integration_biocypher.test_runner:FakeGtdbAdapter")
    offline = run_adapter(cls())
    bundle = build_contribution_bundle(
        offline,
        organization_id="org-test",
        source=source_ref_for_path(source_file),
        schema_version="1.0",
    )
    out = tmp_path / "bundle"
    from micromap_mapforge.integration.biocypher.serialize import serialize_bundle
    serialize_bundle(bundle, out)
    # Smoke: the bundle is well-formed.
    assert (out / "mapping.yaml").exists()
    assert (out / "cypher" / "nodes_OrganismTaxon.cypher").exists()
    assert (out / "manifest.json").exists()


def test_build_contribution_bundle_raises_helpful_error_on_node_arity():
    """Important #7: wrong-arity node tuples produce an actionable error."""

    class BadNodeAdapter:
        name = "bad-node-adapter"
        schema_config = {"prefixes": {}}

        def get_nodes(self):
            # First node is fine; SECOND node forgets `tier` (5-tuple instead of 6)
            yield ("NCBITaxon:9606", "Taxon",
                   {"name": "Homo sapiens"},
                   {"source": "test", "method": "curated"},
                   "EXTRACTED",
                   None)
            yield ("NCBITaxon:9605", "Taxon",
                   {"name": "Homo"},
                   {"source": "test", "method": "curated"},
                   "EXTRACTED")  # missing tier

        def get_edges(self):
            return iter([])

    offline = run_adapter(BadNodeAdapter())
    with pytest.raises(ValueError, match=r"Adapter 'bad-node-adapter' node\[1\] tuple has wrong arity"):
        build_contribution_bundle(
            offline,
            organization_id="org-test",
            source=SourceRef(kind="file", path="/tmp/x.tsv", sha256="0"*64),
            schema_version="1.0",
        )


def test_build_contribution_bundle_raises_helpful_error_on_edge_arity():
    """Important #7: wrong-arity edge tuples produce an actionable error."""

    class BadEdgeAdapter:
        name = "bad-edge-adapter"
        schema_config = {"prefixes": {}}

        def get_nodes(self):
            yield ("NCBITaxon:9606", "Taxon",
                   {"name": "Homo sapiens"},
                   {"source": "test", "method": "curated"},
                   "EXTRACTED",
                   None)

        def get_edges(self):
            # 6-tuple instead of 7 — missing tier
            yield ("MEMBER_OF", "NCBITaxon:9606", "NCBITaxon:9605",
                   {},
                   {"source": "test", "method": "curated"},
                   "EXTRACTED")

    offline = run_adapter(BadEdgeAdapter())
    with pytest.raises(ValueError, match=r"Adapter 'bad-edge-adapter' edge\[0\] tuple has wrong arity"):
        build_contribution_bundle(
            offline,
            organization_id="org-test",
            source=SourceRef(kind="file", path="/tmp/x.tsv", sha256="0"*64),
            schema_version="1.0",
        )


def test_sha256_file_matches_naive_full_read_for_large_file(tmp_path):
    """Chunked hashing must produce the exact same digest as reading the
    whole file at once — proves chunking doesn't change the hash, only
    how much memory computing it needs."""
    import hashlib
    from micromap_mapforge.integration.biocypher.runner import _sha256_file

    # Larger than any reasonable chunk size, so a chunked implementation is
    # actually exercised across multiple reads.
    content = (b"abcdefgh" * 1024) * 500  # 4,096,000 bytes
    p = tmp_path / "big.bin"
    p.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    assert _sha256_file(p) == expected


def test_sha256_tree_matches_naive_full_read_for_large_files(tmp_path):
    from micromap_mapforge.integration.biocypher.runner import _sha256_tree, _sha256_file

    d = tmp_path / "tree"
    d.mkdir()
    (d / "a.bin").write_bytes(b"x" * 3_000_000)
    (d / "b.bin").write_bytes(b"y" * 10)

    # _sha256_tree's own contract (see its docstring): hash 'relpath:digest\n'
    # per file, sorted by POSIX relative path, fed into one sha256.
    h = __import__("hashlib").sha256()
    for rel, digest in sorted([
        ("a.bin", _sha256_file(d / "a.bin")),
        ("b.bin", _sha256_file(d / "b.bin")),
    ]):
        h.update(rel.encode("utf-8"))
        h.update(b":")
        h.update(digest.encode("utf-8"))
        h.update(b"\n")
    assert _sha256_tree(d) == h.hexdigest()
