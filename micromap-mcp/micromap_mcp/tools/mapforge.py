"""MapForge pipeline tools — expose each pipeline pass as an MCP tool.

`register_mapforge_tools` follows the same pattern as `register_kg_tools`:
one function that takes the FastMCP `app` and a `MapForgeRunner` instance,
registers all tools, and returns raw callables when `return_callables=True`.

The source path inside mapping.yaml is resolved relative to `bundle_dir` so
callers don't need to worry about CWD.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import auth
from ..auth import default_org, principal_org
from ..mapforge_runner import MapForgeRunner, MapForgeError

#: A bare built-in template name ("genomics") — anything else handed to
#: mapforge_map_heuristic's schema_config is treated as a path and contained
#: to the bundle base (#316).
_BARE_TEMPLATE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")


def _patch_mapping_source_path(bundle_dir: Path) -> None:
    """Rewrite the source `path` in mapping.yaml to be absolute (bundle-relative)
    so that _load_rows does not depend on CWD."""
    import yaml

    mapping_path = bundle_dir / "mapping.yaml"
    if not mapping_path.exists():
        return
    mapping = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    src_path = mapping.get("source", {}).get("path", "")
    if src_path and not Path(src_path).is_absolute():
        abs_path = (bundle_dir / src_path).resolve()
        mapping["source"]["path"] = str(abs_path)
        mapping_path.write_text(yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8")


def register_mapforge_tools(
    *,
    app,
    runner: MapForgeRunner,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    neo4j_database: str,
    bundle_base: str,
    return_callables: bool = False,
) -> dict:
    """Register all MapForge pipeline tools on `app`.

    Each tool wraps a `MapForgeRunner` method, translating `MapForgeError`
    into a structured error dict so callers get a usable error message.
    """
    from ..bundle_dir import BundleDirManager
    mgr = BundleDirManager(bundle_base)

    def _fmt_error(e: MapForgeError) -> dict:
        return {"error": True, "stage": e.stage, "detail": e.detail}

    def _refusal(kind: str, value: str) -> ValueError:
        # One message for missing AND foreign-owned — a probe with a guessed
        # bundle id must not learn whether it exists (#315).
        return ValueError(
            f"{kind} not found or not owned by this principal: {value!r}"
        )

    def _owned_bundle(bundle_dir: str) -> Path:
        """Resolve bundle_dir and refuse it unless the caller owns it (#315).

        A bundle with no ownership record (pre-#315) belongs to the default
        org, consistent with how #313 treats an unmapped caller.
        """
        resolved = mgr.resolve(bundle_dir)  # ValueError on traversal
        owner = mgr.owner_org(resolved) or default_org()
        if not resolved.is_dir() or owner != principal_org():
            raise _refusal("bundle_dir", bundle_dir)
        return resolved

    def _owned_file(path: str, kind: str) -> Path:
        """Contain a file path to the base (#316) AND to the caller's own
        bundle (#315) — source files staged in another org's bundle are not
        readable."""
        contained = mgr.contain(path, kind=kind)
        rel = contained.relative_to(mgr.base)
        if rel.parts:
            candidate = mgr.base / rel.parts[0]
            if candidate.is_dir():
                owner = mgr.owner_org(candidate) or default_org()
                if owner != principal_org():
                    raise _refusal(kind, path)
        return contained

    def _contained_schema_config(value: str | None) -> str | None:
        """Contain the path form of schema_config to the bundle base (#316).

        Bare built-in template names pass through untouched. Everything else
        — and a bare-looking value that exists as a file, which
        load_schema_config would prefer over the template lookup — resolves
        against the bundle base and must stay inside it (and inside the
        caller's own bundle, #315).
        """
        if value is None:
            return None
        if _BARE_TEMPLATE_NAME.fullmatch(value) and not Path(value).exists():
            return value
        return str(_owned_file(value, "schema_config"))

    async def mapforge_create_bundle() -> dict:
        """Create a new empty bundle directory for a MapForge session.

        Returns the bundle_dir path. Upload your source file and mapping.yaml
        into this directory, then call the pipeline steps in order.

        The bundle is owned by your organization (#315): other orgs cannot
        drive its pipeline steps or read files staged inside it.
        """
        principal = auth.current_principal()
        path = mgr.create_session(
            owner_org=principal_org(),
            owner_user=(principal.user_id if principal else ""),
        )
        return {"bundle_dir": path}

    async def mapforge_inspect(
        source_path: str,
        sheet: str | None = None,
        member: str | None = None,
        table: int | None = None,
        section: str | None = None,
        as_source: bool = False,
    ) -> dict:
        """Inspect a source file and return its schema profile.

        Args:
            source_path: Path to the source inside the bundle base — absolute,
                or relative to the base. Upload sources into a bundle dir from
                mapforge_create_bundle; paths outside the base are refused (#316).
                Besides tabular/document formats, native omics formats are
                profiled directly: .biom, .vcf, .gff/.gff3/.gtf, .bed, .mztab,
                .h5ad/.loom, and a 10x matrix directory (#286).
            sheet: For an .xlsx/.xlsm workbook — the worksheet to profile
                (default: the first non-empty sheet; others are listed in `note`).
            member: For a .zip/.tar.gz archive — the member to profile
                (default: the first; others are listed in `note`).
            table: For a .pdf — the 1-based table index to profile
                (default: the first; others are listed in `note`).
            section: For an .mztab — the section to profile: SML/PRT/PSM/PEP/
                SMF/SME (case-insensitive; default: the primary table, small
                molecules first). An absent section errors, listing those present.
            as_source: For a raw reads/spectra file (.fastq/.bam/.mzML …) — emit
                a provenance-only stub (format, size, sha256, read count under
                `meta`; no columns) so the file can be referenced as a source
                without parsing, instead of the default actionable rejection
                (#286 G8b). No effect on non-raw formats.

        Returns column names, inferred types, row count estimate, and sample
        values; plus `note` (e.g. the mzTab section inventory) and, for a raw
        stub, `meta` (size_bytes/sha256/read_count) when present.
        """
        # Raises ValueError on paths outside the bundle base (#316) or inside
        # another org's bundle (#315) — intentionally not caught, matching
        # the traversal contract of the bundle_dir tools.
        contained = _owned_file(source_path, "source_path")
        try:
            return runner.inspect(str(contained), sheet=sheet, member=member,
                                  table=table, section=section, as_source=as_source)
        except MapForgeError as e:
            return _fmt_error(e)

    async def mapforge_templates_list() -> list[dict]:
        """List all built-in MapForge discipline templates.

        No arguments. Returns a list of {name, version, description} entries,
        sorted by name. Use the returned name with mapforge_templates_show to
        fetch the full schema_config YAML, or pass it to mapforge_map_heuristic's
        schema_config arg to map a source against that discipline.

        The set of available templates depends on the installed
        micromap_mapforge package. Shipped templates are microbiome,
        genomics, transcriptomics, proteomics, and metabolomics.
        """
        try:
            return runner.templates_list()
        except MapForgeError as e:
            return _fmt_error(e)

    async def mapforge_templates_show(name: str) -> dict:
        """Fetch the YAML content of a built-in template by name.

        Args:
            name: Built-in template name. Case-insensitive — "Genomics",
                "GENOMICS", and "genomics" all resolve identically.

        Returns {name: <canonical lowercase>, yaml_content: <full YAML>}.
        On unknown name, returns {error: True, stage: "templates", detail: "..."}
        with the available templates listed in the detail.

        yaml_content is the raw file string with comments preserved.
        """
        try:
            return runner.templates_show(name)
        except MapForgeError as e:
            return _fmt_error(e)

    async def mapforge_map_heuristic(
        source_path: str, schema_config: str | None = None,
    ) -> dict:
        """Draft a heuristic mapping.yaml from the source file's column profile.

        Args:
            source_path: Path to the source file (TSV, CSV, or JSON) inside
                the bundle base — absolute, or relative to the base. Paths
                outside the base are refused (#316).
            schema_config: Optional. Bare built-in template name
                (e.g. "genomics", "transcriptomics") OR path to a custom
                schema_config.yaml inside the bundle base. Default (None)
                uses the package-baked microbiome shape. Use
                mapforge_templates_list to discover available built-in names.

        Returns the mapping.yaml content as a string under the key `mapping_yaml`.
        The source block carries `sha256` sealed at map time (E5 alignment)
        so the bundle remains portable through resolve/emit/submit even if
        the source file moves.

        You can review and adjust the returned YAML, then write it to
        `<bundle_dir>/mapping.yaml` before calling `mapforge_resolve`.
        """
        # Both raise ValueError on paths outside the bundle base (#316) or
        # inside another org's bundle (#315) — intentionally not caught,
        # matching the bundle_dir traversal contract.
        contained_src = _owned_file(source_path, "source_path")
        contained_cfg = _contained_schema_config(schema_config)
        try:
            yaml_str = runner.draft_heuristic(
                str(contained_src), schema_config=contained_cfg,
            )
            return {"mapping_yaml": yaml_str}
        except MapForgeError as e:
            return _fmt_error(e)

    async def mapforge_resolve(bundle_dir: str) -> dict:
        """Resolve source entities against the knowledge graph.

        Requires `<bundle_dir>/mapping.yaml` and the source file it references.
        Writes `resolution.json` to the bundle dir.

        Args:
            bundle_dir: Absolute path to the bundle directory.

        Resolution is scoped to your organization plus the canonical
        reference orgs (#314) — the org comes from the authenticated
        principal, never from an argument.

        Returns resolved_count, unresolved_count, ambiguous_count.
        """
        # Raises ValueError on path traversal or foreign ownership (#315) —
        # intentionally not caught.
        resolved_bd = _owned_bundle(bundle_dir)
        try:
            _patch_mapping_source_path(resolved_bd)
            return runner.resolve(
                str(resolved_bd),
                neo4j_uri=neo4j_uri,
                neo4j_user=neo4j_user,
                neo4j_password=neo4j_password,
                neo4j_database=neo4j_database,
                organization_id=principal_org(),
            )
        except MapForgeError as e:
            return _fmt_error(e)

    async def mapforge_plan(
        bundle_dir: str,
        destination: str = "micromap-core",
    ) -> dict:
        """Plan the ingest route and write routing.yaml to the bundle.

        Args:
            bundle_dir: Absolute path to the bundle directory.
            destination: Target KG (default: "micromap-core").

        The org is taken from the authenticated principal, never from the
        caller (#299) — accepting it as an argument let any caller write data
        stamped as another tenant.

        Returns the routing dict written to routing.yaml.
        """
        organization_id = principal_org()
        # Raises ValueError on path traversal or foreign ownership (#315) —
        # intentionally not caught.
        resolved_bd = _owned_bundle(bundle_dir)
        try:
            return runner.plan(
                str(resolved_bd),
                organization_id=organization_id,
                destination=destination,
            )
        except MapForgeError as e:
            return _fmt_error(e)

    async def mapforge_emit(bundle_dir: str) -> dict:
        """Generate Cypher statements from the resolved bundle.

        Requires `mapping.yaml`, `routing.yaml`, and `resolution.json` in the
        bundle dir. Writes Cypher files to `<bundle_dir>/cypher/`.

        Args:
            bundle_dir: Absolute path to the bundle directory.

        Returns list of generated cypher_files and the bundle_dir.
        """
        # Raises ValueError on path traversal or foreign ownership (#315) —
        # intentionally not caught.
        resolved_bd = _owned_bundle(bundle_dir)
        try:
            return runner.emit(str(resolved_bd))
        except MapForgeError as e:
            return _fmt_error(e)

    async def mapforge_approve(bundle_dir: str, reviewer: str) -> dict:
        """Mark the bundle as human-approved by writing manifest.json.

        Args:
            bundle_dir: Absolute path to the bundle directory.
            reviewer: Name or ID of the approving reviewer.

        Returns reviewer and approved_at timestamp.
        """
        # Raises ValueError on path traversal or foreign ownership (#315) —
        # intentionally not caught.
        resolved_bd = _owned_bundle(bundle_dir)
        try:
            return runner.approve(str(resolved_bd), reviewer=reviewer)
        except MapForgeError as e:
            return _fmt_error(e)

    async def mapforge_submit(bundle_dir: str, reviewer: str) -> dict:
        """Execute the approved Cypher bundle against Neo4j (final pipeline step).

        Requires that `mapforge_approve` was called first (manifest.json must
        have `approved: true`). Writes all nodes and relationships to the KG.

        Args:
            bundle_dir: Absolute path to the bundle directory.
            reviewer: Must match the reviewer set in `mapforge_approve`.

        Returns success, nodes_written, rels_written, contribution_id.
        """
        # Pre-flight approval gate (MCP tool layer, outside try/except so
        # ValueError propagates to the caller rather than being swallowed into
        # a structured-error dict).  The runner enforces the same gate as
        # defense-in-depth for non-MCP callers.
        resolved_bd = _owned_bundle(bundle_dir)  # ValueError on traversal/ownership
        manifest_path = resolved_bd / "manifest.json"
        if not manifest_path.exists():
            raise ValueError("no manifest.json — call mapforge_approve first")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not manifest.get("approved"):
            raise ValueError("bundle not approved — call mapforge_approve first")
        try:
            return runner.submit(
                str(resolved_bd),
                reviewer=reviewer,
                neo4j_uri=neo4j_uri,
                neo4j_user=neo4j_user,
                neo4j_password=neo4j_password,
                neo4j_database=neo4j_database,
            )
        except MapForgeError as e:
            return _fmt_error(e)

    callables = {
        "mapforge_create_bundle": mapforge_create_bundle,
        "mapforge_inspect": mapforge_inspect,
        "mapforge_templates_list": mapforge_templates_list,
        "mapforge_templates_show": mapforge_templates_show,
        "mapforge_map_heuristic": mapforge_map_heuristic,
        "mapforge_resolve": mapforge_resolve,
        "mapforge_plan": mapforge_plan,
        "mapforge_emit": mapforge_emit,
        "mapforge_approve": mapforge_approve,
        "mapforge_submit": mapforge_submit,
    }

    if app is not None:
        for name, fn in callables.items():
            app.tool(name=name)(fn)

    if return_callables:
        return callables
    return {}
