from micromap_mapforge.emit.bundle import IngestBundle
from micromap_mapforge.submit.stubs import RegistryOnlyExecutor


def test_registry_only_submit_returns_receipt(tmp_path):
    bundle = IngestBundle(root=tmp_path)
    executor = RegistryOnlyExecutor()
    receipt = executor.submit(bundle)
    assert receipt.destination == "registry-only"
    assert receipt.success is True
    assert "contribution" in receipt.notes.lower() or "registry" in receipt.notes.lower()
