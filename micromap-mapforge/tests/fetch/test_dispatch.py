"""C3a/C3b (#76): fetch dispatch (classify -> registry, actionable errors)."""
import httpx
import pytest

from micromap_mapforge.fetch import download
from micromap_mapforge.fetch.dispatch import fetch
from micromap_mapforge.fetch.types import FetchError

PAYLOAD = b"x,y\n1,2\n"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr(download, "_resolve_ips", lambda host: ["93.184.216.34"])


def test_routes_url(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=PAYLOAD)))
    fr = fetch("https://example.org/data.csv", dest_dir=tmp_path, client=client)
    assert fr.local_path == tmp_path / "data.csv"


def test_routes_zenodo_via_registry(tmp_path):
    def handler(req):
        if req.url.path == "/api/records/9":
            return httpx.Response(200, json={
                "doi": "10.5281/zenodo.9", "metadata": {"title": "t"},
                "links": {"html": "https://zenodo.org/records/9"},
                "files": [{"key": "d.csv", "size": len(PAYLOAD),
                           "links": {"self": "https://zenodo.org/api/records/9/files/d.csv/content"}}],
            })
        return httpx.Response(200, content=PAYLOAD)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    fr = fetch("doi:10.5281/zenodo.9", dest_dir=tmp_path, client=client)
    assert fr.local_path == tmp_path / "d.csv"
    assert fr.doi == "10.5281/zenodo.9"


def test_unsupported_doi_message(tmp_path):
    with pytest.raises(FetchError, match="not yet supported"):
        fetch("10.1038/nature12373", dest_dir=tmp_path)


def test_unsupported_wb_message(tmp_path):
    with pytest.raises(FetchError, match="micromap-mcp"):
        fetch("wb://proj/data.csv", dest_dir=tmp_path)


def test_local_is_not_fetchable(tmp_path):
    with pytest.raises(FetchError, match="local"):
        fetch("data.csv", dest_dir=tmp_path)


def test_routes_figshare_via_registry(tmp_path):
    def handler(req):
        if req.url.path == "/v2/articles/7":
            return httpx.Response(200, json={
                "title": "t", "doi": "10.6084/m9.figshare.7",
                "figshare_url": "https://figshare.com/articles/dataset/t/7",
                "files": [{"name": "d.csv", "size": len(PAYLOAD),
                           "download_url": "https://ndownloader.figshare.com/files/1"}]})
        return httpx.Response(200, content=PAYLOAD)
    fr = fetch("doi:10.6084/m9.figshare.7", dest_dir=tmp_path,
               client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert fr.local_path == tmp_path / "d.csv"
    assert fr.doi == "10.6084/m9.figshare.7"


def test_routes_dryad_via_registry(tmp_path):
    def handler(req):
        if req.url.raw_path == b"/api/v2/datasets/doi%3A10.5061%2Fdryad.z9":
            return httpx.Response(200, json={
                "title": "t", "_links": {"stash:version": {"href": "/api/v2/versions/5"}}})
        if req.url.path == "/api/v2/versions/5/files":
            return httpx.Response(200, json={"_embedded": {"stash:files": [
                {"path": "d.csv", "size": len(PAYLOAD),
                 "_links": {"stash:download": {"href": "/api/v2/files/1/download"}}}]}})
        return httpx.Response(200, content=PAYLOAD)
    fr = fetch("doi:10.5061/dryad.z9", dest_dir=tmp_path,
               client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert fr.local_path == tmp_path / "d.csv"
    assert fr.doi == "10.5061/dryad.z9"


def test_routes_osf_via_registry(tmp_path):
    def handler(req):
        if "osfstorage" in str(req.url):
            return httpx.Response(200, json={
                "data": [{"attributes": {"name": "d.csv", "size": len(PAYLOAD), "kind": "file"},
                          "links": {"download": "https://files.osf.io/d"}}],
                "links": {"next": None}})
        return httpx.Response(200, content=PAYLOAD)
    fr = fetch("https://osf.io/xy9z/", dest_dir=tmp_path,
               client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert fr.local_path == tmp_path / "d.csv"
    assert fr.doi == "10.17605/OSF.IO/xy9z"
