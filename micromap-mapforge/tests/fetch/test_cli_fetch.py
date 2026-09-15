"""C3a (#76): CLI fetch wiring for `inspect`."""
import json

import httpx
import pytest
import yaml
from click.testing import CliRunner

from micromap_mapforge import cli
from micromap_mapforge.fetch import download

CSV = b"tax_id,abundance\n562,0.31\n853,0.12\n"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["93.184.216.34"])


def _install_mock_client(monkeypatch, handler):
    monkeypatch.setattr(download, "_default_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler)))


def test_inspect_direct_url(monkeypatch, tmp_path):
    _install_mock_client(monkeypatch, lambda req: httpx.Response(200, content=CSV))
    out = tmp_path / "out"
    res = CliRunner().invoke(cli.main, ["inspect", "https://example.org/d.csv", "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert (out / "d.csv").read_bytes() == CSV
    report = json.loads((out / "inspection.json").read_text())
    assert {c["name"] for c in report["columns"]} == {"tax_id", "abundance"}
    fetch_meta = json.loads((out / "fetch.json").read_text())
    assert fetch_meta["url"] == "https://example.org/d.csv"
    assert fetch_meta["doi"] is None


def test_inspect_zenodo_multifile_note(monkeypatch, tmp_path):
    def handler(req):
        if req.url.path == "/api/records/7":
            return httpx.Response(200, json={
                "doi": "10.5281/zenodo.7", "metadata": {"title": "t"},
                "links": {"html": "https://zenodo.org/records/7"},
                "files": [
                    {"key": "a.csv", "size": len(CSV),
                     "links": {"self": "https://zenodo.org/api/records/7/files/a.csv/content"}},
                    {"key": "b.csv", "size": len(CSV),
                     "links": {"self": "https://zenodo.org/api/records/7/files/b.csv/content"}},
                ],
            })
        return httpx.Response(200, content=CSV)
    _install_mock_client(monkeypatch, handler)
    out = tmp_path / "out"
    res = CliRunner().invoke(cli.main, ["inspect", "doi:10.5281/zenodo.7", "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert (out / "a.csv").exists()
    report = json.loads((out / "inspection.json").read_text())
    assert "2 files" in report["note"]


def test_inspect_unsupported_doi_clean_error(monkeypatch, tmp_path):
    _install_mock_client(monkeypatch, lambda req: httpx.Response(200, content=CSV))
    res = CliRunner().invoke(cli.main, ["inspect", "10.1038/nature12373", "--out", str(tmp_path / "o")])
    assert res.exit_code == 2
    assert "not yet supported" in res.output


def test_inspect_missing_local_file_clean_error(tmp_path):
    res = CliRunner().invoke(cli.main, ["inspect", str(tmp_path / "nope.csv"), "--out", str(tmp_path / "o")])
    assert res.exit_code == 2
    assert "no such file" in res.output


def test_map_threads_fetch_attribution_into_mapping(monkeypatch, tmp_path):
    def handler(req):
        if req.url.path == "/api/records/5":
            return httpx.Response(200, json={
                "doi": "10.5281/zenodo.5", "metadata": {"title": "t"},
                "links": {"html": "https://zenodo.org/records/5"},
                "files": [{"key": "d.csv", "size": len(CSV),
                           "links": {"self": "https://zenodo.org/api/records/5/files/d.csv/content"}}],
            })
        return httpx.Response(200, content=CSV)
    _install_mock_client(monkeypatch, handler)
    out = tmp_path / "out"
    res = CliRunner().invoke(
        cli.main, ["map", "doi:10.5281/zenodo.5", "--out", str(out), "--mode", "heuristic"])
    assert res.exit_code == 0, res.output
    mapping = yaml.safe_load((out / "mapping.yaml").read_text())
    src = mapping["source"]
    assert src["url"] == "https://zenodo.org/records/5"
    assert src["doi"] == "10.5281/zenodo.5"
    assert src["accessed_at"]               # stamped
    assert len(src["sha256"]) == 64         # E5 still seals the hash
    assert (out / "d.csv").exists()         # source persisted in the bundle
