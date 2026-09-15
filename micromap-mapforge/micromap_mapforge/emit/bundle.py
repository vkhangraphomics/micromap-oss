"""IngestBundle dataclass + manifest writer."""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class IngestBundle:
    root: Path
    manifest: dict[str, Any] | None = None

    @property
    def mapping_path(self) -> Path:
        return self.root / "mapping.yaml"

    @property
    def routing_path(self) -> Path:
        return self.root / "routing.yaml"

    @property
    def cypher_dir(self) -> Path:
        return self.root / "cypher"

    @property
    def resolution_path(self) -> Path:
        return self.root / "resolution.json"


def build_bundle(root: str | Path) -> IngestBundle:
    return IngestBundle(root=Path(root))


def load_bundle(root: str | Path) -> IngestBundle:
    path = Path(root)
    manifest_file = path / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.exists() else None
    return IngestBundle(root=path, manifest=manifest)


_HASH_CHUNK_SIZE = 1 << 20  # 1 MiB


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(bundle: IngestBundle) -> None:
    files: dict[str, dict[str, Any]] = {}
    for p in sorted(bundle.root.rglob("*")):
        if p.is_file() and p.name != "manifest.json":
            rel = p.relative_to(bundle.root).as_posix()
            files[rel] = {"sha256": _sha256(p), "size": p.stat().st_size}
    manifest: dict[str, Any] = {"files": files, "approved": False}

    # A4 / #74: record schema_config version alongside the file hash. The
    # hash answers "did it change?"; the version answers "what is it
    # semantically?". If schema_config.yaml is missing or unparseable
    # (shouldn't happen post-map, defensive at the emit boundary), the
    # field is omitted rather than failing the manifest write.
    schema_config_path = bundle.root / "schema_config.yaml"
    if schema_config_path.is_file():
        try:
            payload = yaml.safe_load(schema_config_path.read_text(encoding="utf-8"))
            version = (payload or {}).get("version")
            if version:
                manifest["schema_config_version"] = str(version)
        except Exception as exc:
            # File hash above still pins identity; the version field is just
            # human-readable metadata, so we log at debug rather than failing
            # the manifest write.
            logger.debug("schema_config.yaml version field unreadable: %s", exc)

    (bundle.root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    bundle.manifest = manifest
