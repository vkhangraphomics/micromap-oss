"""Library-direct MapForge adapter. Each method maps to one pipeline pass and
raises specific exceptions on failure so the MCP tool layer can translate
them into structured MCP errors."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from neo4j import GraphDatabase
from micromap_mapforge.inspect.dispatch import inspect as _inspect
from micromap_mapforge.inspect.types import SourceProfile
from micromap_mapforge.mapping.mapper import draft_heuristic_mapping
from micromap_mapforge.mapping.validator import validate_mapping
from micromap_mapforge.resolve.pipeline import resolve_mapping, ResolutionReport
from micromap_mapforge.resolve.registry import build_resolvers
from micromap_mapforge.resolve.artifacts import write_artifacts
from micromap_mapforge.resolve.base import Candidate, ResolutionRow
from micromap_mapforge.confidence import Confidence
from micromap_mapforge.emit.routing import plan_route
from micromap_mapforge.emit.bundle import IngestBundle, build_bundle, write_manifest
from micromap_mapforge.emit.report import write_report
from micromap_mapforge.submit.core import MicroMapCoreExecutor


class MapForgeError(Exception):
    """Raised by MapForgeRunner when a pipeline pass fails. `stage` names the
    pass that failed; `detail` carries the library error message."""

    def __init__(self, stage: str, detail: str):
        super().__init__(f"[{stage}] {detail}")
        self.stage = stage
        self.detail = detail


def _source_sha256(path: Path) -> str:
    """Content digest of a source, sealed into mapping.yaml (E5 alignment).

    A single-file source hashes its raw bytes (unchanged). A *directory*
    source — e.g. a 10x matrix (#286 G3b: features/barcodes/matrix triplet) —
    has no single file to hash; ``read_bytes()`` on it raises (IsADirectoryError
    on Linux, PermissionError on Windows). Instead we hash a deterministic
    manifest: for every file under the directory, in sorted POSIX-relative-path
    order, feed ``"<relpath>\\0<file-sha256>\\n"``. Stable across machines and
    portable through resolve/emit/submit, and sensitive to any file's contents.
    """
    if path.is_dir():
        h = hashlib.sha256()
        for f in sorted(p for p in path.rglob("*") if p.is_file()):
            rel = f.relative_to(path).as_posix()
            file_sha = hashlib.sha256(f.read_bytes()).hexdigest()
            h.update(f"{rel}\0{file_sha}\n".encode("utf-8"))
        return h.hexdigest()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_rows(mapping: dict) -> list[dict]:
    src = mapping["source"]
    path = Path(src["path"])
    fmt = src["format"]
    if fmt in ("csv", "tsv"):
        delim = "," if fmt == "csv" else "\t"
        with path.open("r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f, delimiter=delim))
    profile = _inspect(path)
    return list(profile.samples)


def _load_resolution_report(path: Path) -> ResolutionReport:
    data = json.loads(path.read_text(encoding="utf-8"))

    def _mk_row(d: dict) -> ResolutionRow:
        return ResolutionRow(
            entity_label=d["entity_label"],
            source_term=d["source_term"],
            candidates=[
                Candidate(
                    c["node_id"],
                    Confidence(c["match_type"]),
                    c["score"],
                    c["reason"],
                    {},
                    c.get("merge_field", ""),
                    c.get("merge_value", ""),
                )
                for c in d["candidates"]
            ],
        )

    return ResolutionReport(
        resolved=[_mk_row(d) for d in data.get("resolved", [])],
        unresolved=[_mk_row(d) for d in data.get("unresolved", [])],
        ambiguous=[_mk_row(d) for d in data.get("ambiguous", [])],
    )


def _profile_to_dict(profile: SourceProfile) -> dict:
    out = {
        "path": profile.path,
        "format": profile.format,
        "row_count_estimate": profile.row_count_estimate,
        "columns": [
            {
                "name": c.name,
                "inferred_type": c.inferred_type,
                "null_rate": c.null_rate,
                "distinct_count": c.distinct_count,
                "samples": c.samples,
            }
            for c in profile.columns
        ],
    }
    # Surface inspector-level hints only when present — keeps the common tabular
    # profile compact while carrying the mzTab section inventory (`note`) and the
    # #286 G8b raw-stub provenance (`meta`: size/sha256/read_count) over MCP.
    if profile.note:
        out["note"] = profile.note
    if profile.meta is not None:
        out["meta"] = profile.meta
    return out


class MapForgeRunner:
    """Stateless adapter — each method takes its inputs and returns its outputs.
    Bundle dirs are owned by the caller."""

    def templates_list(self) -> list[dict]:
        """Return sorted list of built-in templates with name/version/description.

        Mirrors the JSON output of `mapforge templates list --json` (post-A4).
        Each entry: {"name": str, "version": str, "description": str}.
        Sorted alphabetically by name. Empty list only if no templates ship
        in the installed micromap_mapforge — should not happen in practice.
        """
        from micromap_mapforge.mapping.schema_config import (
            list_builtin_templates,
            builtin_template_path,
        )
        entries: list[dict] = []
        for name in list_builtin_templates():
            path = builtin_template_path(name)
            # path can't be None for a name from list_builtin_templates,
            # but a defensive check costs nothing and keeps the method total.
            if path is None:
                continue
            cfg = _load_yaml(path)
            entries.append({
                "name": name,
                "version": str(cfg.get("version", "")),
                "description": str(cfg.get("description", "")),
            })
        return entries

    def templates_show(self, name: str) -> dict:
        """Return the canonical name + raw YAML content of a built-in template.

        Args:
            name: Built-in template name. Case-insensitive — "Genomics",
                "GENOMICS", "genomics" all resolve identically.

        Returns:
            {"name": <canonical>, "yaml_content": <full file content>}.
            Canonical name is the path's stem (lowercase per templates/
            directory convention). Raw YAML preserves comments.

        Raises:
            MapForgeError(stage="templates"): Unknown name. Detail string
                includes the available templates list so the agent gets
                actionable info.
        """
        from micromap_mapforge.mapping.schema_config import (
            builtin_template_path,
            list_builtin_templates,
        )
        path = builtin_template_path(name)
        if path is None:
            available = ", ".join(list_builtin_templates())
            raise MapForgeError(
                "templates",
                f"unknown template '{name}'. Available: {available}.",
            )
        return {
            "name": path.stem,
            "yaml_content": path.read_text(encoding="utf-8"),
        }

    def inspect(self, source_path: str, *, sheet: str | None = None,
                member: str | None = None, table: int | None = None,
                section: str | None = None, as_source: bool = False) -> dict:
        """Profile a source. The multi-source selectors mirror the CLI `inspect`
        (#286): ``sheet`` (xlsx), ``member`` (archive), ``table`` (pdf),
        ``section`` (mzTab), and ``as_source`` (raw provenance stub)."""
        try:
            profile = _inspect(Path(source_path), sheet=sheet, member=member,
                               table=table, section=section, as_source=as_source)
        except Exception as e:
            raise MapForgeError("inspect", str(e)) from e
        return _profile_to_dict(profile)

    def draft_heuristic(
        self, source_path: str, schema_config: str | None = None,
    ) -> str:
        """Return mapping.yaml content as a string. Does NOT write to disk.

        Args:
            source_path: Absolute path to the source file.
            schema_config: Optional. Bare built-in template name
                (e.g. "genomics") OR absolute path to a custom schema_config.
                Default (None) uses the package-baked microbiome shape.

        Returns:
            mapping.yaml as a YAML-serialized string. The source block carries
            `sha256` sealed at map time (E5 alignment) so the bundle is
            portable through resolve/emit/submit without needing the source
            file to be re-readable at submit time.

        Raises:
            MapForgeError(stage="inspect"): inspect pass failure.
            MapForgeError(stage="map"): schema_config resolution failure,
                heuristic mapper failure, or validate_mapping failure.
                Detail string carries the underlying error message (including
                the alternatives list when SchemaConfigError fires).
        """
        try:
            profile = _inspect(Path(source_path))
        except Exception as e:
            raise MapForgeError("inspect", str(e)) from e
        try:
            # Load schema_config dict if specified (bare name or path).
            cfg = None
            if schema_config:
                from micromap_mapforge.mapping.schema_config import load_schema_config
                cfg = load_schema_config(schema_config)
            # E5 alignment: seal source_sha256 into mapping.yaml so the
            # bundle is portable through resolve/emit/submit without
            # requiring the source file to be re-readable at submit time.
            # A directory source (10x, #286 G3b) is sealed as a manifest digest.
            source_sha = _source_sha256(Path(source_path))
            mapping = draft_heuristic_mapping(
                profile,
                schema_config=cfg,
                source_sha256=source_sha,
            )
            validate_mapping(mapping)
        except Exception as e:
            raise MapForgeError("map", str(e)) from e
        return yaml.safe_dump(mapping, sort_keys=False)

    def resolve(
        self,
        bundle_dir: str,
        *,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        neo4j_database: str,
        organization_id: str | None = None,
    ) -> dict:
        """Pass 3: resolve entities against the knowledge graph.

        ``organization_id`` (#314) scopes the resolver preload to that org
        plus the canonical orgs; ``None`` keeps the unscoped preload for
        non-MCP callers. The MCP tool layer always passes the principal's org.
        """
        try:
            bundle = Path(bundle_dir)
            mapping = _load_yaml(bundle / "mapping.yaml")
            rows = _load_rows(mapping)
            driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
            try:
                resolvers = build_resolvers(driver, database=neo4j_database,
                                            organization_id=organization_id)
                report = resolve_mapping(mapping, rows, resolvers)
            finally:
                driver.close()
            write_artifacts(report, out_dir=bundle)
            return {
                "resolved_count": len(report.resolved),
                "unresolved_count": len(report.unresolved),
                "ambiguous_count": len(report.ambiguous),
            }
        except MapForgeError:
            raise
        except Exception as e:
            raise MapForgeError("resolve", str(e)) from e

    def plan(
        self,
        bundle_dir: str,
        *,
        organization_id: str,
        destination: str = "micromap-core",
    ) -> dict:
        """Pass 4: pick a destination and write routing.yaml."""
        try:
            bundle = Path(bundle_dir)
            mapping = _load_yaml(bundle / "mapping.yaml")
            routing = plan_route(
                mapping,
                organization_id=organization_id,
                destination_override=destination,
            )
            (bundle / "routing.yaml").write_text(
                yaml.safe_dump(routing, sort_keys=False), encoding="utf-8"
            )
            return routing
        except MapForgeError:
            raise
        except Exception as e:
            raise MapForgeError("plan", str(e)) from e

    def emit(self, bundle_dir: str) -> dict:
        """Pass 5: generate Cypher from the resolved bundle via the IR path."""
        try:
            bundle = Path(bundle_dir)
            mapping = _load_yaml(bundle / "mapping.yaml")
            routing = _load_yaml(bundle / "routing.yaml")
            rows = _load_rows(mapping)
            report = _load_resolution_report(bundle / "resolution.json")
            organization_id = routing["organization_id"]
            provenance_enabled = (routing.get("provenance") or {}).get("enabled", True)

            # 3b-3a: IR-only emit path (emit/cypher.py was retired in #136).
            from micromap_mapforge.integration.biocypher.ir import SourceRef
            from micromap_mapforge.integration.biocypher.serialize import serialize_bundle
            from micromap_mapforge.integration.tabular import tabular_to_ir

            source = mapping["source"]
            source_path = Path(source["path"])
            if not source_path.is_absolute():
                source_path = (bundle / source_path).resolve()
            # E5 alignment: prefer the sha sealed into mapping.yaml at map
            # time (post-E5 the runner's draft_heuristic always seals it).
            # Fall back to re-reading the source file only for legacy
            # pre-E5 bundles where the sealed value is absent. This keeps
            # the SourceRef sha consistent with mapping.yaml's sealed value
            # whenever the bundle is post-E5, even if the source file has
            # been moved between map and emit.
            sealed_sha = source.get("sha256")
            if sealed_sha:
                source_sha = sealed_sha
            else:
                source_sha = _source_sha256(source_path)
            source_ref = SourceRef(kind="file", path=str(source_path), sha256=source_sha)

            schema_config = None
            schema_config_path = bundle / "schema_config.yaml"
            if schema_config_path.exists():
                from micromap_mapforge.mapping.schema_config import load_schema_config
                schema_config = load_schema_config(schema_config_path)

            ir_bundle = tabular_to_ir(
                mapping=mapping, rows=rows, report=report,
                organization_id=organization_id, source_ref=source_ref,
                schema_config=schema_config,
            )
            serialize_bundle(ir_bundle, out_dir=bundle, include_provenance=provenance_enabled)

            b = build_bundle(bundle)
            write_report(b, report)
            write_manifest(b)

            cypher_dir = bundle / "cypher"
            cypher_files = sorted(p.name for p in cypher_dir.iterdir()) if cypher_dir.is_dir() else []
            return {"cypher_files": cypher_files, "bundle_dir": str(bundle)}
        except MapForgeError:
            raise
        except Exception as e:
            raise MapForgeError("emit", str(e)) from e

    def approve(self, bundle_dir: str, *, reviewer: str) -> dict:
        """Mark the bundle as approved — write/update manifest.json."""
        try:
            bundle = Path(bundle_dir)
            manifest_path = bundle / "manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            else:
                manifest = {}
            approved_at = datetime.now(timezone.utc).isoformat()
            manifest["approved"] = True
            manifest["reviewer"] = reviewer
            manifest["approved_at"] = approved_at
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return {"reviewer": reviewer, "approved_at": approved_at}
        except MapForgeError:
            raise
        except Exception as e:
            raise MapForgeError("approve", str(e)) from e

    def submit(
        self,
        bundle_dir: str,
        *,
        reviewer: str,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        neo4j_database: str,
    ) -> dict:
        """Pass 6: execute the Cypher bundle against Neo4j."""
        try:
            bundle = Path(bundle_dir)
            # Approval gate: defense-in-depth in case a caller bypasses
            # the MCP tool layer (which also gates) and calls the runner directly.
            manifest_path = bundle / "manifest.json"
            if not manifest_path.exists():
                raise MapForgeError(
                    "submit",
                    f"bundle not approved — manifest.json missing in {bundle_dir}"
                )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not manifest.get("approved"):
                raise MapForgeError(
                    "submit",
                    "bundle not approved — call mapforge_approve first"
                )
            ingest = IngestBundle(root=bundle)
            driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
            try:
                executor = MicroMapCoreExecutor(driver, database=neo4j_database)
                receipt = executor.submit(ingest)
            finally:
                driver.close()
            return {
                "success": receipt.success,
                "nodes_written": receipt.nodes_written,
                "rels_written": receipt.relationships_written,
                "contribution_id": getattr(receipt, "contribution_id", None),
            }
        except MapForgeError:
            raise
        except Exception as e:
            raise MapForgeError("submit", str(e)) from e
