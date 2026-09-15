"""MicroMap MapForge CLI. Subcommands: inspect, map, resolve, plan, emit, submit."""

import hashlib
import io
import json
import sys
from dataclasses import replace as _dc_replace
from pathlib import Path

import click

from .fetch.dispatch import fetch as fetch_remote
from .fetch.identifier import classify as classify_source
from .inspect.dispatch import inspect as run_inspect
from .inspect.types import SourceProfile


def _configure_streams_for_unicode() -> None:
    """Ensure sys.stdout/sys.stderr can encode the non-ASCII characters used in
    Click help text and our own echo output.

    Background (#115): Click's ``echo()`` writes help/echo strings directly via
    ``file.write()``. On Windows the default stdout codec is cp1252, which has
    no mapping for characters that legitimately appear in our help text
    (``→`` in ``import-kg``'s docstring, em-dashes in flag help). ``--help``
    therefore crashed with ``UnicodeEncodeError`` before users saw any output.

    We detect the situation by probing whether the current stream encoding can
    encode a representative non-ASCII character; if it can't, we reconfigure to
    UTF-8 with ``errors='replace'`` (so even an unexpected character produces
    ``?`` rather than crashing). On Linux/macOS with the default UTF-8 streams
    this is a no-op. Some test or subprocess contexts wrap stdout in objects
    that don't support ``reconfigure``; we tolerate that silently.
    """
    sentinel = "→"  # → — used in the import-kg subcommand docstring.
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        encoding = getattr(stream, "encoding", None)
        if not encoding:
            continue
        try:
            sentinel.encode(encoding)
        except (UnicodeEncodeError, LookupError):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is None:
                continue
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, io.UnsupportedOperation, OSError):
                pass


_configure_streams_for_unicode()


@click.group()
def main() -> None:
    """MicroMap MapForge — produce reviewable ingestion bundles from data sources."""


@main.command("inspect")
@click.argument("source")
@click.option(
    "--out",
    type=click.Path(file_okay=False),
    default="mapforge-out",
    show_default=True,
    help="Output directory for the bundle so far.",
)
@click.option(
    "--sheet",
    default=None,
    help="For .xlsx/.xlsm workbooks: the worksheet to profile (default: the "
         "first non-empty sheet; other sheets are listed in the report).",
)
@click.option(
    "--member",
    default=None,
    help="For .zip/.tar.gz archives: the inspectable member to profile "
         "(default: the first; other members are listed in the report).",
)
@click.option(
    "--table",
    type=int,
    default=None,
    help="For .pdf sources: the 1-based table index to profile (default: the "
         "first; other tables are listed in the report).",
)
@click.option(
    "--section",
    default=None,
    help="For .mztab sources: the section to profile — SML/PRT/PSM/PEP/SMF/SME "
         "(case-insensitive; default: the primary table, small molecules first; "
         "other sections are listed in the report note).",
)
@click.option(
    "--as-source",
    "as_source",
    is_flag=True,
    default=False,
    help="For raw reads/spectra (.fastq .bam .mzML …): emit a provenance-only "
         "stub (format, size, sha256, read count) so the file can be referenced "
         "as a source, instead of the default actionable rejection (#286 G8b). "
         "No effect on non-raw formats.",
)
@click.option(
    "--file",
    "file_",
    default=None,
    help="For a Zenodo deposit: which file in the record to fetch "
         "(default: the first; others are listed in the report).",
)
def inspect_cmd(source: str, out: str, sheet: str | None, member: str | None,
                table: int | None, section: str | None, as_source: bool,
                file_: str | None) -> None:
    """Run Pass 1 Inspector on SOURCE and emit an inspection report.

    SOURCE is a local path, a direct URL, or a Zenodo/Figshare/Dryad/OSF DOI. Remote sources are
    downloaded into --out before profiling (C3a, #76).

    Accepted formats: tabular/document (.csv .tsv .json .jsonl .ndjson .parquet
    .sql .xlsx .xlsm .pdf), omics (.biom .vcf .gff/.gff3/.gtf .bed .mztab .h5ad
    .loom, a 10x matrix directory), and archives (.zip .tar[.gz|.bz2|.xz] .tgz).
    Single-file .gz/.bz2/.xz are decompressed transparently (e.g. study.csv.gz).
    Raw reads/spectra (.fastq .bam .cram .mzML …) are rejected with a pointer to
    the pipeline that produces a mappable artifact (#286 G8a), unless --as-source
    is given, which emits a provenance-only stub instead (#286 G8b).
    """
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)

    fetched = None
    try:
        if classify_source(source).kind == "local":
            # a local source is a file, or a directory (a 10x matrix dir, #286 G3b)
            if not Path(source).exists():
                click.echo(f"error: {source}: no such file or directory", err=True)
                sys.exit(2)
            local = source
        else:
            fetched = fetch_remote(source, file=file_, dest_dir=out_dir)
            local = str(fetched.local_path)
        profile = run_inspect(local, sheet=sheet, member=member, table=table,
                              section=section, as_source=as_source)
    except ValueError as e:   # FetchError is a ValueError subclass
        click.echo(f"error: {e}", err=True)
        sys.exit(2)

    # Record fetch provenance + surface the Zenodo deposit inventory (if any).
    if fetched is not None:
        if fetched.note:
            merged = f"{fetched.note} | {profile.note}" if profile.note else fetched.note
            profile = _dc_replace(profile, note=merged)
        (out_dir / "fetch.json").write_text(
            json.dumps(fetched.to_dict(), indent=2), encoding="utf-8"
        )

    (out_dir / "inspection.json").write_text(
        json.dumps(profile.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )
    (out_dir / "inspection-report.md").write_text(
        _render_report(profile), encoding="utf-8"
    )
    click.echo(f"wrote {out_dir}/inspection-report.md")


def _render_report(profile: SourceProfile) -> str:
    lines = [
        "# Inspection Report",
        "",
        f"- **Source:** `{profile.path}`",
        f"- **Format:** `{profile.format}`",
        f"- **Row count (estimate):** {profile.row_count_estimate}",
        f"- **Columns:** {len(profile.columns)}",
        *([f"- **Note:** {profile.note}"] if profile.note else []),
        # #286 G8b: a raw provenance stub carries structured identity, not columns.
        *([f"- **Size (bytes):** {profile.meta['size_bytes']}",
           f"- **sha256:** `{profile.meta['sha256']}`",
           f"- **Read count:** {profile.meta.get('read_count')}"]
          if profile.meta else []),
        "",
        "## Columns",
        "",
        "| Name | Inferred Type | Null Rate | Distinct | Samples |",
        "|---|---|---|---|---|",
    ]
    for c in profile.columns:
        samples = ", ".join(repr(s) for s in c.samples[:3])
        lines.append(
            f"| `{c.name}` | {c.inferred_type} | {c.null_rate:.2%} | "
            f"{c.distinct_count} | {samples} |"
        )

    structured = [c for c in profile.columns if c.structured]
    if structured:
        lines += ["", "## Structured columns (candidates for decomposition)", ""]
        for c in structured:
            s = c.structured
            if s.get("kind") == "rank_lineage":
                detail = f"rank lineage on '{s['delimiter']}' → {', '.join(s['components'])}"
            else:
                detail = f"{s.get('kind')} on '{s['delimiter']}' → {s.get('parts')} parts"
            lines.append(f"- `{c.name}`: {detail}")

    return "\n".join(lines) + "\n"


from typing import Any as _Any

import yaml as _yaml

from .mapping.mapper import draft_heuristic_mapping, propose_mapping
from .mapping.validator import MappingValidationError


def _build_anthropic_client() -> _Any:
    """Indirection so tests can patch this without importing anthropic."""
    import anthropic
    return anthropic.Anthropic()


@main.command("map")
@click.argument("source")
@click.option(
    "--out",
    type=click.Path(file_okay=False),
    default="mapforge-out",
    show_default=True,
)
@click.option(
    "--mode",
    type=click.Choice(["heuristic", "llm"]),
    default="heuristic",
    show_default=True,
    help="heuristic = offline column-hint draft; llm = Claude-assisted proposal.",
)
@click.option("--hint", default=None, help="Optional natural-language hint for --mode llm.")
@click.option(
    "--schema-config",
    "schema_config_value",
    type=str,
    default=None,
    help="Built-in template name OR path to a project-level schema_config.yaml "
         "(LinkML + x_mapforge extensions; Theme A1'/A2', #74). Run "
         "`mapforge templates list` to see available built-ins. Omit to use "
         "the microbiome default. The resolved schema_config is copied into "
         "the bundle so resolve/plan/emit see the same one.",
)
@click.option(
    "--file",
    "file_",
    default=None,
    help="For a Zenodo deposit: which file in the record to fetch "
         "(default: the first).",
)
def map_cmd(source: str, out: str, mode: str, hint: str | None,
            schema_config_value: str | None, file_: str | None) -> None:
    """Run Pass 1 Mapper on SOURCE and emit a mapping.yaml draft.

    SOURCE is a local path, a direct URL, or a Zenodo/Figshare/Dryad/OSF DOI. Remote sources are
    downloaded into --out, and their url/doi/accessed_at are threaded into the
    mapping.yaml source block alongside the E5 source_sha256 (C3a, #76).
    """
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)

    fetched = None
    try:
        if classify_source(source).kind == "local":
            # a local source is a file, or a directory (a 10x matrix dir, #286 G3b)
            if not Path(source).exists():
                click.echo(f"error: {source}: no such file or directory", err=True)
                sys.exit(2)
            local = source
        else:
            fetched = fetch_remote(source, file=file_, dest_dir=out_dir)
            local = str(fetched.local_path)
        profile = run_inspect(local)
    except ValueError as e:   # FetchError is a ValueError subclass
        click.echo(f"error: {e}", err=True)
        sys.exit(2)

    # Theme A1'/A2' (#74): the --schema-config value may be a built-in
    # template name OR a file path. The loader does the resolution; we just
    # surface validation errors as exit 3 with a clean single-line message.
    from .mapping.schema_config import (
        DEFAULT_SCHEMA_CONFIG_PATH,
        SchemaConfigError,
        builtin_template_path,
        load_schema_config,
    )
    try:
        schema_config = load_schema_config(schema_config_value)
    except SchemaConfigError as e:
        # SchemaConfigError messages already start with "schema_config:"; don't
        # double-prefix.
        click.echo(f"error: {e}", err=True)
        sys.exit(3)
    except FileNotFoundError as e:
        click.echo(f"error: schema_config: {e}", err=True)
        sys.exit(3)

    # For the bundle copy (below), we need the source PATH the loader
    # resolved to. This duplicates the loader's resolution logic; A4's
    # versioning work should either return the resolved path from the
    # loader or expose a companion resolve_schema_config_path() helper so
    # the two branches can't drift.
    if schema_config_value is None:
        schema_config_source: Path = DEFAULT_SCHEMA_CONFIG_PATH
    else:
        as_path = Path(schema_config_value)
        if as_path.exists() and as_path.is_file():
            schema_config_source = as_path
        else:
            # builtin_template_path returns the resolved path. We know
            # load_schema_config succeeded above, so resolution must have
            # gone through this branch — None here would be an invariant
            # violation (raised, not asserted, so -O doesn't elide it).
            resolved = builtin_template_path(schema_config_value)
            if resolved is None:
                raise RuntimeError(
                    "load_schema_config succeeded but builtin_template_path "
                    "returned None — invariant violation"
                )
            schema_config_source = resolved

    # E5 (#78): seal source_sha256 into mapping.yaml at map time so submit
    # can populate Contribution.source_sha256 without re-opening the source
    # file. The source file is already open (via run_inspect above); reading
    # bytes a second time is the simplest implementation. For large sources
    # (>1 GB), this adds proportional wall-clock time; not a concern for
    # typical CSV/JSON sources.
    source_sha256 = hashlib.sha256(Path(local).read_bytes()).hexdigest()

    try:
        if mode == "heuristic":
            mapping = draft_heuristic_mapping(profile, schema_config=schema_config,
                                              source_sha256=source_sha256)
        else:
            client = _build_anthropic_client()
            mapping = propose_mapping(profile, hint=hint, client=client,
                                      schema_config=schema_config,
                                      source_sha256=source_sha256)
    except MappingValidationError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(3)
    except Exception as e:
        # LLM transport errors — timeout, rate limit, network — produce a
        # clean single-line message instead of a Python traceback. The
        # specific Anthropic SDK error classes vary; identifying by class
        # name keeps this branch decoupled from a hard import of anthropic.
        cls_name = type(e).__name__
        if cls_name in (
            "APITimeoutError", "RateLimitError", "APIConnectionError",
            "AuthenticationError", "PermissionDeniedError",
            "InternalServerError", "APIStatusError",
            "BadRequestError", "NotFoundError",
        ):
            hint = {
                "APITimeoutError":      "LLM request timeout",
                "RateLimitError":       "LLM rate limit exceeded",
                "APIConnectionError":   "cannot connect to LLM provider (network/DNS/TLS)",
                "AuthenticationError":  "LLM authentication failed — check ANTHROPIC_API_KEY",
                "PermissionDeniedError": "LLM permission denied for this model/account",
                "InternalServerError":  "LLM provider internal server error",
                "APIStatusError":       "LLM provider returned an error status",
                "BadRequestError":      "LLM provider rejected the request payload",
                "NotFoundError":        "LLM model not found",
            }.get(cls_name, cls_name)
            click.echo(f"error: {hint}: {e}", err=True)
            sys.exit(3)
        raise

    # C3a (#76): a fetched source seeds its own provenance — url/doi/accessed_at
    # into the mapping source block, beside the sha256 E5 already sealed. setdefault
    # so a user-authored value is never overwritten.
    if fetched is not None:
        src_block = mapping.setdefault("source", {})
        if fetched.url:
            src_block.setdefault("url", fetched.url)
        if fetched.doi:
            src_block.setdefault("doi", fetched.doi)
        src_block.setdefault("accessed_at", fetched.accessed_at)

    mapping_path = out_dir / "mapping.yaml"
    mapping_path.write_text(
        _yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8"
    )

    # Also write the inspection report (map implies inspect)
    (out_dir / "inspection.json").write_text(
        json.dumps(profile.to_dict(), indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "inspection-report.md").write_text(
        _render_report(profile), encoding="utf-8"
    )

    # Theme A1' / #74: copy the resolved schema_config into the bundle so
    # downstream resolve/plan/emit (and future mapper/resolver retargets in
    # #135) see the same one. No copy when the user already placed a
    # schema_config.yaml in out_dir — they're explicitly overriding.
    # The schema_config was already loaded and validated above; here we
    # just copy the source bytes so the bundle's file is byte-identical
    # to what the user provided (or the package default).
    bundle_schema_path = out_dir / "schema_config.yaml"
    if not bundle_schema_path.exists():
        bundle_schema_path.write_bytes(schema_config_source.read_bytes())

    click.echo(f"wrote {mapping_path} ({mode} mode)")


# -- M2 subcommands --

import csv as _csv

from .resolve.artifacts import write_artifacts
from .resolve.pipeline import resolve_mapping
from .emit.routing import plan_route


def _notification_suppression_kwargs() -> dict:
    """Driver kwargs that silence UNRECOGNIZED-class notifications (#79 F2).

    ``build_resolvers`` issues ``MATCH (n:Label)`` for every in-scope project
    label. On a partly-populated graph, a label referenced by the bundle but
    absent from the graph emits an ``01N42`` UNRECOGNIZED notification per
    query; at MicroMap's label count these flood stderr and bury real warnings.
    #117 narrowed *which* labels are queried, but a referenced-yet-absent label
    still triggers the flood — so suppress that notification class at the driver.

    The kwarg was renamed ``notifications_disabled_categories`` →
    ``notifications_disabled_classifications`` in neo4j 5.22 (the enum landed in
    the same release). Detect by import so the declared floor (``neo4j>=5.15``)
    keeps working without a deprecation warning on modern drivers.
    """
    try:
        from neo4j import NotificationDisabledClassification

        return {
            "notifications_disabled_classifications": [
                NotificationDisabledClassification.UNRECOGNIZED
            ]
        }
    except ImportError:  # neo4j 5.15 - 5.21
        from neo4j import NotificationDisabledCategory

        return {
            "notifications_disabled_categories": [
                NotificationDisabledCategory.UNRECOGNIZED
            ]
        }


def _build_resolvers(uri: str, user: str, password: str, database: str,
                     labels_of_interest: set[str] | None = None,
                     schema_config: dict | None = None,
                     driver_factory=None):
    """Build resolver registry from a live Neo4j. Patchable in tests.

    ``labels_of_interest`` (#117): when provided, the registry preload skips
    every project label not in the set. The caller (``resolve_cmd``) extracts
    the labels from the bundle's ``mapping.entities[].label`` so we don't
    issue ``MATCH (n:Gene)``-style queries for labels the bundle never touches.

    ``schema_config`` (A1' slice 3, #74): the project's ontology, loaded from
    ``<bundle>/schema_config.yaml`` by the caller. ``None`` falls back to the
    package-baked default — preserves pre-slice-3 behavior.

    ``driver_factory`` (#79 F2): the callable used to open the driver, defaults
    to ``GraphDatabase.driver``. Injectable so tests can assert the resolve
    driver is built with notification suppression (see
    ``_notification_suppression_kwargs``).
    """
    from neo4j import GraphDatabase

    from .resolve.registry import build_resolvers

    if driver_factory is None:
        driver_factory = GraphDatabase.driver
    driver = driver_factory(
        uri, auth=(user, password), **_notification_suppression_kwargs()
    )
    return build_resolvers(
        driver, database=database,
        labels_of_interest=labels_of_interest,
        schema_config=schema_config,
    )


def _mapping_labels(mapping: dict) -> set[str]:
    """Collect entity labels declared in the bundle's mapping.

    These are the only labels the resolver needs to preload — relationship
    anchors always reference entities also declared in ``entities[]``, so
    this set covers both lookup paths.
    """
    return {entity["label"] for entity in mapping.get("entities", []) if "label" in entity}


def _load_rows(mapping: dict, bundle_dir: Path) -> list[dict]:
    """Read source rows using the mapping.source.format/path.

    Relative `source.path` values are anchored to `bundle_dir`, matching the
    convention used by `_write_contribution_for_bundle`. The shipped
    examples/disbiome/mapping.yaml uses a relative path (`disbiome_sample.csv`);
    without this anchoring, resolve would fail with FileNotFoundError when run
    from a CWD other than the bundle dir. (#116)

    Per-format dispatch returns the FULL row set, not the inspector's
    ``SAMPLE_CAP``-truncated sample. (#123) — the earlier fallback that called
    ``inspect(path)`` and returned ``profile.samples`` silently dropped every
    row past the first 5 for JSON/JSONL/Parquet/SQL-dump sources, a
    high-severity data-loss bug surfaced by the #65 HMDB-scale validation.
    """
    src = mapping["source"]
    path = Path(src["path"])
    if not path.is_absolute():
        path = bundle_dir / path
    fmt = src["format"]

    if fmt in ("csv", "tsv"):
        delim = "," if fmt == "csv" else "\t"
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = _csv.DictReader(f, delimiter=delim)
            return list(reader)
    if fmt == "json":
        from .inspect.json_files import load_rows_json
        return load_rows_json(path)
    if fmt == "jsonl":
        from .inspect.json_files import load_rows_jsonl
        return load_rows_jsonl(path)
    if fmt == "parquet":
        from .inspect.parquet import load_rows_parquet
        return load_rows_parquet(path)
    if fmt == "sql_dump":
        from .inspect.sql_dump import load_rows_sql_dump
        return load_rows_sql_dump(path)

    raise ValueError(f"unsupported source format for resolve/emit: {fmt!r}")


@main.command("resolve")
@click.option("--bundle", type=click.Path(exists=True, file_okay=False), required=True,
              help="Bundle directory containing mapping.yaml.")
@click.option("--neo4j-uri", required=True)
@click.option("--neo4j-user", required=True)
@click.option("--neo4j-password", required=True)
@click.option("--neo4j-database", default="neo4j", show_default=True)
def resolve_cmd(bundle: str, neo4j_uri: str, neo4j_user: str,
                neo4j_password: str, neo4j_database: str) -> None:
    """Run Pass 3 (resolve) against Neo4j and write resolution.json + unresolved.md."""
    bundle_dir = Path(bundle)
    mapping_path = bundle_dir / "mapping.yaml"
    if not mapping_path.exists():
        click.echo(f"error: {mapping_path} not found; run 'mapforge map' first", err=True)
        sys.exit(2)

    mapping = _yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    rows = _load_rows(mapping, bundle_dir)

    # A1' slice 3 (#74): load the bundle's schema_config if present, so the
    # resolver registry preloads via the project ontology. Absent → registry
    # falls back to the package-baked default (pre-A1' behavior).
    schema_config: dict | None = None
    schema_config_path = bundle_dir / "schema_config.yaml"
    if schema_config_path.exists():
        from .mapping.schema_config import load_schema_config
        schema_config = load_schema_config(schema_config_path)

    resolvers = _build_resolvers(
        neo4j_uri, neo4j_user, neo4j_password, neo4j_database,
        labels_of_interest=_mapping_labels(mapping),
        schema_config=schema_config,
    )
    report = resolve_mapping(mapping, rows, resolvers)
    write_artifacts(report, bundle_dir)
    click.echo(
        f"resolved={report.resolved_count} unresolved={report.unresolved_count} "
        f"ambiguous={report.ambiguous_count}"
    )


@main.command("plan")
@click.option("--bundle", type=click.Path(exists=True, file_okay=False), required=True)
@click.option("--organization-id", required=True)
@click.option("--destination", default=None,
              type=click.Choice(["micromap-core", "registry-only", "new-federated-instance"]),
              help="Override policy-selected destination.")
@click.option("--policy", "policy_path", default=None,
              type=click.Path(exists=True, dir_okay=False),
              help="Path to routing-policy.yaml. If omitted, falls back to 'micromap-core'.")
@click.option("--contributor", "contributor_path", default=None,
              type=click.Path(exists=True, dir_okay=False),
              help="Path to contributor.yaml. Defaults to <bundle>/contributor.yaml if present.")
@click.option("--tier", default=None, type=click.Choice(["internal", "partner", "external"]),
              help="Overrides contributor.yaml tier.")
@click.option("--sensitivity", default=None, type=click.Choice(["public", "internal", "pii"]),
              help="Overrides contributor.yaml sensitivity.")
@click.option("--row-count", default=None, type=int,
              help="Bundle row count for size-based routing rules.")
def plan_cmd(bundle: str, organization_id: str, destination: str | None,
             policy_path: str | None, contributor_path: str | None,
             tier: str | None, sensitivity: str | None,
             row_count: int | None) -> None:
    """Run Pass 4 routing and write routing.yaml."""
    bundle_dir = Path(bundle)
    mapping_path = bundle_dir / "mapping.yaml"
    if not mapping_path.exists():
        click.echo(f"error: {mapping_path} not found; run 'mapforge map' first", err=True)
        sys.exit(2)
    mapping = _yaml.safe_load(mapping_path.read_text(encoding="utf-8"))

    from .contributor.validator import load_contributor, validate_contributor

    try:
        contributor: dict[str, _Any] = {}
        _contributor_loaded = False
        default_contrib = bundle_dir / "contributor.yaml"
        if contributor_path:
            contributor = load_contributor(contributor_path)
            _contributor_loaded = True
        elif default_contrib.exists():
            contributor = load_contributor(default_contrib)
            _contributor_loaded = True
        # CLI flags override contributor fields.
        if tier is not None:
            contributor["tier"] = tier
            _contributor_loaded = True
        if sensitivity is not None:
            contributor["sensitivity"] = sensitivity
            _contributor_loaded = True
        if "contributor" not in contributor:
            contributor["contributor"] = organization_id
        if _contributor_loaded:
            # Re-validate after overrides to catch invalid combinations.
            validate_contributor(contributor)

        policy = None
        if policy_path:
            from .emit.routing_policy import load_routing_policy
            policy = load_routing_policy(policy_path)
    except ValueError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(3)

    routing = plan_route(
        mapping,
        organization_id=organization_id,
        destination_override=destination,
        policy=policy,
        contributor=contributor or None,
        row_count=row_count,
    )
    routing_path = bundle_dir / "routing.yaml"
    routing_path.write_text(_yaml.safe_dump(routing, sort_keys=False), encoding="utf-8")
    click.echo(f"wrote {routing_path} (destination={routing['destination']})")


# -- M2 Pass 5 subcommands: emit, submit --

from .confidence import Confidence
from .emit.bundle import build_bundle, load_bundle, write_manifest
from .emit.report import write_report
from .resolve.base import Candidate, ResolutionRow
from .resolve.pipeline import ResolutionReport
from .submit.factory import executor_for_destination


def _load_resolution_report(path: Path) -> ResolutionReport:
    data = json.loads(path.read_text(encoding="utf-8"))

    def _mk_row(d):
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


@main.command("emit")
@click.option("--bundle", type=click.Path(exists=True, file_okay=False), required=True)
def emit_cmd(bundle: str) -> None:
    """Run Pass 5 ArtifactEmitter: write cypher/, INGEST_REPORT.md, manifest.json.

    3b-3a (#127): the legacy ``emit/cypher.py`` path was retired; ``emit``
    always routes through the Contribution Bundle IR + serializer now. The
    ``--ir`` flag from 3b-1 is gone — it's the only path.
    """
    bundle_dir = Path(bundle)
    for name, suggestion in [
        ("mapping.yaml", "run 'mapforge map' first"),
        ("routing.yaml", "run 'mapforge plan' first"),
        ("resolution.json", "run 'mapforge resolve' first"),
    ]:
        if not (bundle_dir / name).exists():
            click.echo(f"error: {bundle_dir / name} not found; {suggestion}", err=True)
            sys.exit(2)

    mapping = _yaml.safe_load((bundle_dir / "mapping.yaml").read_text(encoding="utf-8"))
    routing = _yaml.safe_load((bundle_dir / "routing.yaml").read_text(encoding="utf-8"))
    rows = _load_rows(mapping, bundle_dir)
    report = _load_resolution_report(bundle_dir / "resolution.json")

    organization_id = routing["organization_id"]
    # 3b-2 (#126): honor routing.yaml::provenance.enabled. Absence defaults
    # to True — matches submit-side default; existing bundles keep their
    # edge-level provenance.
    provenance_enabled = (routing.get("provenance") or {}).get("enabled", True)
    _emit_via_ir(
        mapping, rows, report, organization_id, bundle_dir,
        include_provenance=provenance_enabled,
    )

    b = build_bundle(bundle_dir)
    write_report(b, report)
    write_manifest(b)

    click.echo(f"emitted bundle at {bundle_dir}")


def _emit_via_ir(
    mapping: dict,
    rows: list[dict],
    report,                     # ResolutionReport
    organization_id: str,
    bundle_dir: Path,
    *,
    include_provenance: bool = True,
) -> None:
    """3b-1 (#125) path: build a ContributionBundle via tabular_to_ir() and
    serialize it via the 3a serializer, replacing the legacy direct write.

    The serializer regenerates mapping.yaml and routing.yaml in place; for an
    existing bundle dir this is intentional — the IR is now the single source
    of truth and the on-disk files are a serialization of it.

    `include_provenance` controls whether provenance_* keys are written onto
    node/edge property payloads — sourced from routing.yaml::provenance.enabled
    by the caller (3b-2, #126). Confidence and tier are never affected.
    """
    from .integration.biocypher.ir import SourceRef
    from .integration.biocypher.serialize import serialize_bundle
    from .integration.tabular import tabular_to_ir

    source = mapping["source"]
    source_path = Path(source["path"])
    if not source_path.is_absolute():
        source_path = bundle_dir / source_path
    sealed_sha = source.get("sha256")
    if source_path.is_dir():
        if sealed_sha:
            source_sha = sealed_sha
        else:
            # tree-hash: sha256 of sorted (relpath, sha256) entries, mirroring
            # the spec §F2 fix the BioCypher producer uses for archive sources.
            parts: list[str] = []
            for p in sorted(source_path.rglob("*")):
                if p.is_file():
                    rel = p.relative_to(source_path).as_posix()
                    parts.append(f"{rel}:{hashlib.sha256(p.read_bytes()).hexdigest()}")
            source_sha = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
        source_ref = SourceRef(kind="dir", path=str(source_path), sha256=source_sha)
    else:
        source_sha = sealed_sha if sealed_sha else hashlib.sha256(source_path.read_bytes()).hexdigest()
        source_ref = SourceRef(kind="file", path=str(source_path), sha256=source_sha)

    # Theme A1' / #74: if the bundle has a schema_config.yaml (written by
    # `mapforge map`), embed it on the IR so consumers and downstream
    # producers see the project's ontology. Absent → tabular_to_ir uses
    # its placeholder dict (backward-compat for bundles created before A1').
    schema_config: dict | None = None
    schema_config_path = bundle_dir / "schema_config.yaml"
    if schema_config_path.exists():
        from .mapping.schema_config import load_schema_config
        schema_config = load_schema_config(schema_config_path)

    bundle = tabular_to_ir(
        mapping=mapping, rows=rows, report=report,
        organization_id=organization_id, source_ref=source_ref,
        schema_config=schema_config,
    )
    serialize_bundle(bundle, out_dir=bundle_dir, include_provenance=include_provenance)


def _executor_for_bundle(routing: dict, neo4j_uri: str | None, neo4j_user: str | None,
                         neo4j_password: str | None, neo4j_database: str):
    """Build executor from routing.yaml. Returns (executor, driver_or_None).
    Caller is responsible for closing the driver if non-None."""
    dest = routing["destination"]
    if dest == "micromap-core":
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
        return executor_for_destination(dest, driver=driver, database=neo4j_database), driver
    return executor_for_destination(dest), None


# -- M3: Contribution writer for the CLI submit path --


def _usage_error_for_missing_sha(exc: Exception) -> click.UsageError:
    """Translate a MissingSourceShaError into the CLI's verbose, actionable
    UsageError (exit 2). Single source of the message text so the submit path
    and the _resolve_source_sha_for_submit shim stay consistent."""
    return click.UsageError(
        "submit: source_sha256 is not sealed in this bundle's mapping.yaml "
        "AND the source file is not present at submit time. Re-run `mapforge map` "
        "to seal the sha into mapping.yaml, or place the source file at the "
        "recorded path. Refusing to write a Contribution node with empty "
        "source_sha256 -- that corrupts the audit trail by MERGE-collision."
    )


def _resolve_source_sha_for_submit(
    bundle_dir: Path, mapping: dict,
) -> str:
    """Resolve source_sha256 for `mapforge submit`.

    E5 (#78) seals the SHA into mapping.yaml at map time. Submit prefers
    the sealed value; falls back to recomputing from the on-disk file for
    bundles built before E5; raises ``click.UsageError`` (exit 2) when
    neither is available.

    Refusing to fall back to an empty string is the whole point: an empty
    source_sha256 causes Contribution MERGE collisions across submissions
    that silently corrupt the audit trail. Loud failure is the right
    failure mode here.

    Shim: delegates to ``submit.service.resolve_source_sha`` and re-raises
    ``MissingSourceShaError`` as ``click.UsageError`` so callers that import
    this function directly (including tests pinning exit-2 / UsageError) see
    the same exception type as before.
    """
    from .submit.service import MissingSourceShaError, resolve_source_sha

    try:
        return resolve_source_sha(bundle_dir, mapping)
    except MissingSourceShaError as exc:
        # `or {}` handles both absent and explicitly-None `source` keys, since
        # `dict.get("source", {})` returns the stored None on `{"source": None}`
        # and the subsequent `.get` would crash. Bundles without a sealed sha
        # AND without a source file present at submit time reach this branch —
        # schema validation alone does not prevent it.
        raise _usage_error_for_missing_sha(exc) from exc


def _write_contribution_for_bundle(
    bundle_dir: Path,
    routing: dict,
    reviewer: str,
    receipt,                                 # SubmissionReceipt
    neo4j_uri: str, neo4j_user: str,
    neo4j_password: str, neo4j_database: str,
    *,
    force_submitted: bool | None = None,
) -> None:
    """Write a Contribution node to MicroMap core after a successful submit."""
    from neo4j import GraphDatabase

    from .provenance.contribution import write_contribution
    from .submit.service import MissingSourceShaError, build_contribution_record

    try:
        record = build_contribution_record(
            bundle_dir, routing, reviewer, receipt, force_submitted=force_submitted,
        )
    except MissingSourceShaError as exc:
        raise _usage_error_for_missing_sha(exc) from exc
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    try:
        write_contribution(driver, record, database=neo4j_database)
    finally:
        driver.close()


def _write_decision_for_bundle(
    bundle_dir: Path, routing: dict, receipt,
    neo4j_uri: str, neo4j_user: str, neo4j_password: str, neo4j_database: str,
    *, about_tags: list[str], summary: str | None, rationale: str,
    actor: str | None,
) -> int:
    """Record a :Decision{data_contribution} for this submission, linked by
    :RECORDS to the :Contribution and by :ABOUT to its subject entities.

    Returns the number of ABOUT edges. Raises on Neo4j errors — the caller wraps
    this so a provenance failure never gates the already-completed data write.
    """
    from neo4j import GraphDatabase

    from .provenance.decision import write_decision
    from .submit.service import MissingSourceShaError, build_decision_record

    try:
        record = build_decision_record(
            bundle_dir, routing, receipt,
            about_tags=about_tags, summary=summary,
            rationale=rationale, actor=actor,
        )
    except MissingSourceShaError as exc:
        raise _usage_error_for_missing_sha(exc) from exc
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    try:
        return write_decision(driver, record, database=neo4j_database)
    finally:
        driver.close()


@main.command("submit")
@click.option("--bundle", type=click.Path(exists=True, file_okay=False), required=True)
# E4 (#78): help text intentionally omits the audit-stamp side effect — when
# --force is used to bypass an unapproved bundle, the resulting :Contribution
# node is stamped `force_submitted: true`. The stamp is a graph-layer concern,
# not a CLI-output concern. See docs/provenance.md §"Governance properties".
@click.option("--force", is_flag=True, help="Submit without manifest approval.")
@click.option("--reviewer", default=None,
              help="Reviewer name — required when routing-policy has "
                   "provenance.enabled=true (the default). Silently ignored "
                   "when provenance is disabled.")
@click.option("--neo4j-uri", default=None)
@click.option("--neo4j-user", default=None)
@click.option("--neo4j-password", default=None)
@click.option("--neo4j-database", default="neo4j", show_default=True)
# Decision provenance (#190): record a :Decision{data_contribution} for this
# upload, linked to the :Contribution and the entities it's about. Non-gating —
# a failure here never blocks the data write.
@click.option("--about", "about", multiple=True,
              help="Canonical subject tag(s) for the decision's ABOUT edges: a "
                   "Disease name (e.g. 'parkinson disease') or a Taxon id "
                   "(e.g. 'NCBITaxon:853'). Repeatable or comma-separated. "
                   "Disease subjects are also auto-derived from resolution.json.")
@click.option("--decision-summary", default=None,
              help="One-line summary for the decision (defaults to a generated "
                   "'MapForge ingest: <source> (...)' line).")
@click.option("--rationale", default="", help="Why this contribution was made.")
@click.option("--actor", default=None,
              help="Acting user/agent (defaults to the routing contributor).")
@click.option("--no-decision", is_flag=True,
              help="Skip the decision-provenance record (still writes data + "
                   "Contribution).")
def submit_cmd(bundle: str, force: bool, reviewer: str | None,
               neo4j_uri: str | None, neo4j_user: str | None,
               neo4j_password: str | None, neo4j_database: str,
               about: tuple[str, ...], decision_summary: str | None,
               rationale: str, actor: str | None, no_decision: bool) -> None:
    """Execute the bundle against its routed destination.

    Writes a Contribution provenance node afterwards when
    `routing.yaml::provenance.enabled` is True (the default). For stand-alone
    curated KGs (`provenance: { enabled: false }`) the Contribution write is
    skipped — see `docs/new-instance-executor-contract.md` §"Stand-alone
    curated KGs — provenance opt-out".
    """
    bundle_dir = Path(bundle)
    b = load_bundle(bundle_dir)
    if b.manifest is None:
        click.echo("error: no manifest.json — run 'mapforge emit' first", err=True)
        sys.exit(4)
    approved = b.manifest.get("approved", False)
    if not approved and not force:
        click.echo(
            "error: bundle not approved. Run 'mapforge approve --bundle ... --reviewer ...' "
            "or pass --force.",
            err=True,
        )
        sys.exit(5)

    routing = _yaml.safe_load((bundle_dir / "routing.yaml").read_text(encoding="utf-8"))
    provenance_enabled = routing.get("provenance", {}).get("enabled", True)

    # Reviewer is only required when we'll actually write a Contribution node.
    manifest_reviewer = (b.manifest or {}).get("reviewer")
    effective_reviewer = reviewer or manifest_reviewer
    if provenance_enabled:
        if not effective_reviewer:
            click.echo(
                "error: --reviewer <name> is required (or run 'mapforge approve' "
                "to record the reviewer in the manifest).",
                err=True,
            )
            sys.exit(8)
    elif reviewer or manifest_reviewer:
        click.echo(
            "--reviewer ignored: provenance writer disabled by routing-policy",
            err=True,
        )

    # Neo4j credentials required for the destination write. Contribution write
    # (when enabled) reuses the same driver.
    missing = [n for n, v in [("--neo4j-uri", neo4j_uri),
                              ("--neo4j-user", neo4j_user),
                              ("--neo4j-password", neo4j_password)] if not v]
    if missing:
        click.echo(f"error: submit requires {', '.join(missing)} "
                   f"(needed for the destination write)", err=True)
        sys.exit(7)

    executor, executor_driver = _executor_for_bundle(routing, neo4j_uri, neo4j_user, neo4j_password, neo4j_database)
    try:
        receipt = executor.submit(b)
    finally:
        if executor_driver is not None:
            executor_driver.close()
    click.echo(
        f"submitted: destination={receipt.destination} "
        f"nodes={receipt.nodes_written} rels={receipt.relationships_written} "
        f"notes={receipt.notes}"
    )
    if not receipt.success:
        sys.exit(6)

    if provenance_enabled:
        # E4 (#78): stamp force_submitted=True on the :Contribution node
        # when the gate was actually bypassed (unapproved bundle AND
        # --force flag). Approved-then-submitted produces force_submitted=
        # None, which the Cypher writer omits from the SET clause entirely.
        # --force passed on an already-approved bundle is treated as no-op
        # (no stamp) -- the gate wasn't bypassed.
        force_submitted_flag = True if (not approved and force) else None
        _write_contribution_for_bundle(
            bundle_dir, routing, effective_reviewer, receipt,
            neo4j_uri, neo4j_user, neo4j_password, neo4j_database,
            force_submitted=force_submitted_flag,
        )
        click.echo("wrote Contribution node for this submission")

        # Decision provenance (#190). NON-GATING: the data + Contribution are
        # already written above; a failure here only warns. Provenance must
        # never block a push to a MapForge KG.
        if not no_decision:
            # --about accepts repeats and/or comma-separated values.
            about_tags = [t.strip() for chunk in about for t in chunk.split(",") if t.strip()]
            try:
                n_about = _write_decision_for_bundle(
                    bundle_dir, routing, receipt,
                    neo4j_uri, neo4j_user, neo4j_password, neo4j_database,
                    about_tags=about_tags, summary=decision_summary,
                    rationale=rationale, actor=actor,
                )
                click.echo(
                    f"recorded Decision (data_contribution) — ABOUT "
                    f"{n_about} entit{'y' if n_about == 1 else 'ies'}"
                )
            except Exception as exc:
                click.echo(
                    f"warning: decision provenance not recorded "
                    f"({type(exc).__name__}: {exc}); data + Contribution already "
                    f"written, submission stands",
                    err=True,
                )
    else:
        click.echo("provenance writer disabled by routing-policy", err=True)


@main.command("approve")
@click.option("--bundle", type=click.Path(exists=True, file_okay=False), required=True)
@click.option("--reviewer", required=True, help="Reviewer identity recorded in manifest.")
def approve_cmd(bundle: str, reviewer: str) -> None:
    """Mark the bundle approved for submission and record the reviewer."""
    from datetime import datetime, timezone

    bundle_dir = Path(bundle)
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.exists():
        click.echo(f"error: {manifest_path} not found; run 'mapforge emit' first", err=True)
        sys.exit(4)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["approved"] = True
    manifest["reviewer"] = reviewer
    manifest["approved_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    click.echo(f"approved by {reviewer} at {manifest['approved_at']}")


# -- 3a: BioCypher integration --

# -- License-aware import enforcement (C6.1, #76) shared options + policy ---

def _license_options(f):
    """Attach the deployment license-policy options to a command."""
    f = click.option("--policy", "policy_file", default=None,
                     type=click.Path(exists=True, dir_okay=False),
                     help="License policy manifest (YAML).")(f)
    f = click.option("--commercial", is_flag=True, default=False,
                     help="This deployment makes commercial use (gates "
                          "restricted/prohibited/unknown KGs).")(f)
    f = click.option("--allow-restricted", is_flag=True, default=False,
                     help="Permit 'restricted' KGs under a commercial policy.")(f)
    f = click.option("--allow-unknown", is_flag=True, default=False,
                     help="Permit 'unknown' commercial-posture KGs.")(f)
    return f


def _build_license_policy(policy_file, commercial, allow_restricted, allow_unknown):
    from .catalog.license_policy import LicensePolicy, load_policy
    if policy_file:
        base = load_policy(policy_file)
        return LicensePolicy(
            commercial=commercial or base.commercial,
            allow_restricted=allow_restricted or base.allow_restricted,
            allow_unknown=allow_unknown or base.allow_unknown,
            allowlist=base.allowlist,
            denylist=base.denylist,
        )
    return LicensePolicy(commercial=commercial, allow_restricted=allow_restricted,
                         allow_unknown=allow_unknown)


@main.command("import-kg")
@click.argument("adapter_path")
@click.option("--from", "from_id", default=None,
              help="Catalog KG id (see `mapforge sources list`) — runs the "
                   "license gate against the deployment policy before import.")
@_license_options
@click.option("--source", "source_path", required=True,
              type=click.Path(exists=True),
              help="Source data file/dir/archive that the adapter reads.")
@click.option("--organization-id", required=True,
              help="Multi-tenant isolation tag for the bundle.")
@click.option("--out", required=True, type=click.Path(file_okay=False),
              help="Bundle output directory.")
@click.option("--schema-mode", default="adopt", show_default=True,
              type=click.Choice(["adopt", "adapt", "transform"]),
              help="'adopt' uses the adapter's own schema_config verbatim. "
                   "'adapt' remaps its labels onto --schema-config via "
                   "input_label. 'transform' (multi-adapter chaining) isn't "
                   "built yet (#76 C7 follow-up).")
@click.option("--schema-config", "schema_config_arg", default=None,
              help="Project schema_config (file path or built-in template "
                   "name) whose classes' input_label fields remap the "
                   "adapter's raw labels. Required with --schema-mode=adapt.")
def import_kg_cmd(
    adapter_path: str,
    source_path: str,
    organization_id: str,
    out: str,
    schema_mode: str,
    schema_config_arg: str | None,
    from_id: str | None,
    policy_file: str | None,
    commercial: bool,
    allow_restricted: bool,
    allow_unknown: bool,
) -> None:
    """Run a BioCypher adapter offline → IR → bundle.

    ADAPTER_PATH is a dotted path of the form 'module.submodule:ClassName'.
    """
    if schema_mode == "transform":
        click.echo(
            "error: --schema-mode=transform (multi-adapter chaining) isn't "
            "built yet — tracked as a #76 C7 follow-up",
            err=True,
        )
        sys.exit(2)
    if schema_mode == "adapt" and schema_config_arg is None:
        click.echo(
            "error: --schema-mode=adapt requires --schema-config (the "
            "project schema_config whose input_label fields remap the "
            "adapter's raw labels)",
            err=True,
        )
        sys.exit(2)

    # License gate (C6.1): when --from names a catalog KG, refuse the import if
    # the deployment's license policy doesn't permit it (e.g. commercial use of
    # a paid-license source). Skipped entirely when --from is omitted.
    if from_id is not None:
        from .catalog import get_source
        from .catalog.license_policy import evaluate

        source = get_source(from_id)
        if source is None:
            from .catalog import list_source_ids
            click.echo(
                f"error: '{from_id}' is not in the catalog. "
                f"Available: {', '.join(list_source_ids())}.",
                err=True,
            )
            sys.exit(2)
        policy = _build_license_policy(policy_file, commercial, allow_restricted, allow_unknown)
        decision = evaluate(source, policy)
        if not decision.allowed:
            click.echo(f"refused: {decision.reason}", err=True)
            sys.exit(3)
        click.echo(f"license OK: {decision.reason}")

    from micromap_mapforge.integration.biocypher.runner import (
        AdapterLoadError,
        build_contribution_bundle,
        load_adapter,
        run_adapter,
        source_ref_for_path,
    )
    from micromap_mapforge.integration.biocypher.serialize import serialize_bundle

    try:
        cls = load_adapter(adapter_path)
    except AdapterLoadError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(3)

    # Locked decision (plan §Phase 4 front-matter, 2026-06-02): real BioCypher
    # adapters follow the `__init__(path)` convention. Inspect the signature
    # to decide whether to pass `path` — a bare `try ... except TypeError`
    # would swallow any TypeError raised from INSIDE the adapter's __init__
    # body (review finding #7), silently masking real adapter bugs by
    # retrying with no args.
    import inspect as _inspect
    sig_params = _inspect.signature(cls).parameters
    takes_path = "path" in sig_params or any(
        p.kind is _inspect.Parameter.VAR_KEYWORD for p in sig_params.values()
    )
    adapter = cls(path=source_path) if takes_path else cls()

    out_dir = Path(out)

    if schema_mode == "adapt":
        from micromap_mapforge.integration.biocypher.schema_adapt import (
            UnmappedLabelError,
            remap_offline_adapter,
        )
        from micromap_mapforge.mapping.schema_config import (
            SchemaConfigError,
            load_schema_config,
        )
        offline = run_adapter(adapter)
        try:
            project_schema_config = load_schema_config(schema_config_arg)
        except (SchemaConfigError, FileNotFoundError) as exc:
            click.echo(f"error: schema_config: {exc}", err=True)
            sys.exit(3)
        try:
            offline = remap_offline_adapter(offline, project_schema_config)
        except UnmappedLabelError as exc:
            click.echo(f"error: {exc}", err=True)
            sys.exit(3)
        bundle = build_contribution_bundle(
            offline,
            organization_id=organization_id,
            source=source_ref_for_path(source_path),
            schema_version="1.0",
        )
        serialize_bundle(bundle, out_dir)
        click.echo(
            f"wrote bundle at {out_dir} "
            f"(nodes={len(bundle.nodes)} edges={len(bundle.edges)})"
        )
    else:
        from micromap_mapforge.integration.biocypher.streaming import stream_adapter_to_bundle
        stats = stream_adapter_to_bundle(
            adapter,
            out_dir,
            organization_id=organization_id,
            source=source_ref_for_path(source_path),
            schema_version="1.0",
        )
        click.echo(
            f"wrote bundle at {out_dir} "
            f"(nodes={stats.nodes_written} edges={stats.edges_written})"
        )


# -- `mapforge templates` --------------------------------------------------
# Theme A2' / #74 slice 4: discovery surface for the built-in discipline
# templates shipped under mapping/templates/.


@main.group("templates")
def templates_group() -> None:
    """Discover and inspect built-in discipline templates."""


@templates_group.command("list")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit JSON instead of the default human-readable table.",
)
def templates_list_cmd(as_json: bool) -> None:
    """List every built-in discipline template with version + description."""
    from .mapping.schema_config import (
        builtin_template_path,
        list_builtin_templates,
    )
    entries: list[dict[str, str]] = []
    for name in list_builtin_templates():
        path = builtin_template_path(name)
        # path is guaranteed non-None — list_builtin_templates only yields
        # names that resolve. The assert documents this for readers; if a
        # future change violates it, the next line crashes loudly anyway.
        assert path is not None
        try:
            payload = _yaml.safe_load(path.read_text(encoding="utf-8"))
            version = str(payload.get("version", ""))
            description = str(payload.get("description", ""))
        except Exception:
            version = ""
            description = ""
        entries.append({"name": name, "version": version, "description": description})

    if as_json:
        import json
        click.echo(json.dumps(entries, indent=2))
        return

    # 3-column text format: name (padded), v<version>, description.
    if not entries:
        return
    name_width = max(len(e["name"]) for e in entries)
    for entry in entries:
        click.echo(
            f"{entry['name']:<{name_width}}  v{entry['version']}  {entry['description']}"
        )


@templates_group.command("show")
@click.argument("name")
def templates_show_cmd(name: str) -> None:
    """Print the YAML of a built-in discipline template to stdout."""
    from .mapping.schema_config import (
        builtin_template_path,
        list_builtin_templates,
    )
    path = builtin_template_path(name)
    if path is None:
        available = ", ".join(list_builtin_templates())
        click.echo(
            f"error: '{name}' is not a built-in template. "
            f"Available: {available}.",
            err=True,
        )
        sys.exit(2)
    click.echo(path.read_text(encoding="utf-8"), nl=False)


# -- `mapforge sources` ----------------------------------------------------
# C6 (#76): curated catalog of open-source biomedical KGs (license + adapter
# posture + schema preview) to bootstrap a customer's graph from.


@main.group("sources")
def sources_group() -> None:
    """Browse the curated catalog of open-source biomedical knowledge graphs."""


@sources_group.command("list")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Emit JSON instead of the table.")
@click.option("--commercial-ok", is_flag=True, default=False,
              help="Only sources whose commercial_use is 'allowed'.")
@click.option("--adapter", "adapter_status", default=None,
              help="Filter by adapter_status "
                   "(upstream|contributed|needs_contribution|unknown).")
def sources_list_cmd(as_json: bool, commercial_ok: bool, adapter_status: str | None) -> None:
    """List catalog KGs with license + adapter posture."""
    from .catalog import load_catalog

    rows = load_catalog()
    if commercial_ok:
        rows = [s for s in rows if s.commercial_use == "allowed"]
    if adapter_status:
        rows = [s for s in rows if s.adapter_status == adapter_status]

    if as_json:
        import json
        click.echo(json.dumps([s.to_dict() for s in rows], indent=2))
        return
    if not rows:
        return
    id_w = max(len(s.id) for s in rows)
    lic_w = max(len(s.license) for s in rows)
    com_w = max(len(s.commercial_use) for s in rows)
    for s in rows:
        click.echo(
            f"{s.id:<{id_w}}  {s.license:<{lic_w}}  {s.commercial_use:<{com_w}}  "
            f"{s.adapter_status:<18}  {s.name}"
        )


@sources_group.command("show")
@click.argument("source_id")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Emit JSON instead of the formatted view.")
def sources_show_cmd(source_id: str, as_json: bool) -> None:
    """Show full metadata for a catalog KG by id."""
    from .catalog import catalog_caveat, get_source, list_source_ids

    s = get_source(source_id)
    if s is None:
        click.echo(
            f"error: '{source_id}' is not in the catalog. "
            f"Available: {', '.join(list_source_ids())}.",
            err=True,
        )
        sys.exit(2)
    if as_json:
        import json
        click.echo(json.dumps(s.to_dict(), indent=2))
        return
    click.echo(f"# {s.name}  ({s.id})")
    click.echo(f"homepage:        {s.homepage}")
    click.echo(f"description:     {s.description}")
    click.echo(f"license:         {s.license}  (commercial_use: {s.commercial_use})")
    click.echo(f"license_notes:   {s.license_notes}")
    click.echo(f"adapter:         {s.adapter_status}  {s.adapter_location}".rstrip())
    click.echo(f"biolink:         {', '.join(s.biolink_categories)}")
    click.echo(f"recommended_for: {', '.join(s.recommended_for)}")
    click.echo(f"est_size:        {s.est_size}")
    click.echo(f"last_validated:  {s.last_validated}")
    if s.notes:
        click.echo(f"notes:           {s.notes}")
    click.echo("")
    click.echo(f"NOTE: {catalog_caveat()}")


@sources_group.command("check")
@click.argument("source_id")
@_license_options
def sources_check_cmd(source_id: str, policy_file: str | None, commercial: bool,
                      allow_restricted: bool, allow_unknown: bool) -> None:
    """Check whether a catalog KG may be imported under the license policy.

    Exit 0 = allowed, 3 = refused, 2 = unknown id. The default policy is
    non-commercial (academic) — pass --commercial (or a --policy manifest) to
    gate against the deployment's commercial-use rights.
    """
    from .catalog import get_source, list_source_ids
    from .catalog.license_policy import evaluate

    source = get_source(source_id)
    if source is None:
        click.echo(
            f"error: '{source_id}' is not in the catalog. "
            f"Available: {', '.join(list_source_ids())}.",
            err=True,
        )
        sys.exit(2)
    policy = _build_license_policy(policy_file, commercial, allow_restricted, allow_unknown)
    decision = evaluate(source, policy)
    verb = "ALLOWED" if decision.allowed else "REFUSED"
    click.echo(f"{verb}: {source.id} — {decision.reason}")
    sys.exit(0 if decision.allowed else 3)


# -- `mapforge diff` -------------------------------------------------------
# C8 (#76): incremental delta between two versions of a bundle (e.g. Reactome
# v89 -> v90), so customers ingest only what changed.


@main.command("diff")
@click.argument("old", type=click.Path(exists=True))
@click.argument("new", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Emit the full delta as JSON instead of the summary.")
def diff_cmd(old: str, new: str, as_json: bool) -> None:
    """Diff two bundle versions (incremental KG update).

    OLD and NEW are bundle directories (each carrying a bundle.ir.json snapshot
    written at emit/import time) or snapshot files directly.
    """
    from .versioning import diff_bundle_dirs

    try:
        diff = diff_bundle_dirs(old, new)
    except FileNotFoundError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(diff.to_dict(), indent=2, default=str))
        return

    s = diff.summary()
    click.echo(f"nodes: +{s['nodes_added']} -{s['nodes_removed']} ~{s['nodes_changed']}")
    click.echo(f"edges: +{s['edges_added']} -{s['edges_removed']} ~{s['edges_changed']}")
    if diff.is_empty():
        click.echo("no changes")


# -- `mapforge propose-schema` ---------------------------------------------
# D2 (#77): LLM drafts a BioCypher schema_config from a dataset + domain hint.


@main.command("propose-schema")
@click.argument("source", type=click.Path(exists=True, dir_okay=False))
@click.option("--hint", default=None,
              help="Domain context, e.g. 'CRISPR off-target effects in plant pathogens'.")
@click.option("--base-template", default=None,
              help="Built-in discipline template to use as a style reference "
                   "(see `mapforge templates list`).")
@click.option("--out", default="schema_config.proposed.yaml", show_default=True,
              type=click.Path(dir_okay=False),
              help="Where to write the drafted schema_config.yaml.")
def propose_schema_cmd(source: str, hint: str | None, base_template: str | None, out: str) -> None:
    """Draft a BioCypher schema_config.yaml for SOURCE (D2, #77).

    Inspects the source, asks the LLM to propose Biolink categories + CURIE
    prefixes + relationships, surfaces Bioregistry prefix conflicts, and writes a
    draft for you to review. Requires ANTHROPIC_API_KEY.
    """
    from .inspect.dispatch import inspect as run_inspect
    from .mapping.schema_config import load_schema_config
    from .mapping.schema_proposer import propose_schema

    try:
        profile = run_inspect(source)
    except ValueError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(2)

    base = None
    if base_template:
        try:
            base = load_schema_config(base_template)
        except Exception as e:
            click.echo(f"error: --base-template {base_template!r}: {e}", err=True)
            sys.exit(2)

    proposal = propose_schema(profile, hint=hint, base_template=base)

    out_path = Path(out)
    out_path.write_text(_yaml.safe_dump(proposal.schema_config, sort_keys=False), encoding="utf-8")
    click.echo(f"wrote draft schema_config to {out_path}")
    classes = list((proposal.schema_config.get("classes") or {}).keys())
    click.echo(f"classes: {', '.join(classes)}")
    if proposal.rationale:
        click.echo(f"rationale: {proposal.rationale}")
    for w in proposal.warnings:
        click.echo(f"warning: {w}", err=True)
    if proposal.conflicts:
        click.echo("Bioregistry conflicts (review before use):", err=True)
        for c in proposal.conflicts:
            sug = f" — did you mean '{c['suggestion']}'?" if c.get("suggestion") else ""
            click.echo(f"  - '{c['prefix']}' ({c['where']}) not in Bioregistry{sug}", err=True)


@templates_group.command("diff")
@click.argument("name")
@click.option("--from", "from_version", required=True,
              help="Older version to diff from (e.g. 1.0.0).")
@click.option("--to", "to_version", required=True,
              help="Newer version to diff to (e.g. 1.1.0).")
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Emit the full delta as JSON instead of the summary.")
def templates_diff_cmd(name: str, from_version: str, to_version: str, as_json: bool) -> None:
    """Semantic diff between two versions of a built-in discipline template.

    Templates are versioned in place (see `templates version`), so an older
    version's body is resolved from git history rather than a shipped archive;
    the current version is read straight off disk.
    """
    from .mapping.template_diff import (
        VersionNotFoundError,
        resolve_version_payload,
        semantic_diff,
    )

    try:
        old = resolve_version_payload(name, from_version)
        new = resolve_version_payload(name, to_version)
    except VersionNotFoundError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    diff = semantic_diff(old, new)
    if as_json:
        click.echo(json.dumps(diff, indent=2))
        return

    click.echo(f"{name}: {from_version} -> {to_version}")
    if diff["classes_added"]:
        click.echo(f"  classes added: {', '.join(diff['classes_added'])}")
    if diff["classes_removed"]:
        click.echo(f"  classes removed: {', '.join(diff['classes_removed'])}")
    if diff["classes_changed"]:
        click.echo(f"  classes changed: {', '.join(diff['classes_changed'])}")
        for cls_name, entry in diff["classes_changed"].items():
            click.echo(f"    {cls_name}:")
            if entry.get("identifiers_added"):
                click.echo(f"      + identifiers: {', '.join(entry['identifiers_added'])}")
            if entry.get("identifiers_removed"):
                click.echo(f"      - identifiers: {', '.join(entry['identifiers_removed'])}")
            if entry.get("notes_changed"):
                click.echo("      notes updated")
            if entry.get("other_fields_changed"):
                click.echo(f"      other fields changed: {', '.join(entry['other_fields_changed'])}")
    if not (diff["classes_added"] or diff["classes_removed"] or diff["classes_changed"]):
        click.echo("  no changes")


@templates_group.command("version")
@click.argument("name")
def templates_version_cmd(name: str) -> None:
    """Print the version: field of a built-in discipline template."""
    from .mapping.schema_config import (
        builtin_template_path,
        list_builtin_templates,
    )
    path = builtin_template_path(name)
    if path is None:
        available = ", ".join(list_builtin_templates())
        click.echo(
            f"error: '{name}' is not a built-in template. "
            f"Available: {available}.",
            err=True,
        )
        sys.exit(2)
    payload = _yaml.safe_load(path.read_text(encoding="utf-8"))
    click.echo(payload.get("version", ""))


# -- Fabric federation --

@main.command("register-constituent")
@click.option("--composite", required=True,
              help="Name of the Neo4j Fabric composite database (e.g. 'graphomics').")
@click.option("--name", required=True,
              help="Alias name for this constituent inside the composite "
                   "(e.g. 'primekg'). The alias is created as <composite>.<name>.")
@click.option("--database", required=True,
              help="Name of the constituent Neo4j database to point to.")
@click.option("--neo4j-uri", required=True,
              help="Bolt URI of the Enterprise Neo4j instance hosting the composite.")
@click.option("--neo4j-user", required=True)
@click.option("--neo4j-password", required=True)
def register_constituent_cmd(
    composite: str, name: str, database: str,
    neo4j_uri: str, neo4j_user: str, neo4j_password: str,
) -> None:
    """Register a Neo4j database as a constituent of a Fabric composite database.

    Runs:
        CREATE ALIAS `<composite>.<name>` IF NOT EXISTS FOR DATABASE <database>

    on the system database of the target Enterprise Neo4j instance.

    Use this after 'mapforge submit' (or 'mapforge import-kg') to make a freshly
    ingested database queryable via the composite endpoint — the final step in
    the 'build a federated graph from scratch' workflow:

        mapforge bootstrap → mapforge import-kg / submit → mapforge register-constituent
        → point the Neo4j MCP at the composite → agent traverses all constituents

    Requires Neo4j Enterprise (composite databases are Enterprise-only).
    Idempotent: IF NOT EXISTS means re-running is safe.
    """
    from neo4j import GraphDatabase

    alias = f"{composite}.{name}"
    # The composite namespace and the constituent name must be quoted as TWO
    # separate identifiers (`composite`.`name`), NOT as one quoted dotted string
    # (`composite.name`). The latter creates a standalone database alias whose
    # literal name happens to contain a dot (composite association = NULL) — it
    # never becomes a constituent, so `USE composite.name` fails with "Graph not
    # found". Quoting each part keeps the dot as the namespace separator and
    # still tolerates hyphens/dots in either part; escape embedded backticks.
    escaped_composite = composite.replace("`", "``")
    escaped_name = name.replace("`", "``")
    escaped_db = database.replace("`", "``")
    cypher = (
        f"CREATE ALIAS `{escaped_composite}`.`{escaped_name}` "
        f"IF NOT EXISTS FOR DATABASE `{escaped_db}`"
    )

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    try:
        with driver.session(database="system") as session:
            session.run(cypher)
            # Verify it landed.
            result = session.run(
                "SHOW DATABASES YIELD name, constituents "
                "WHERE name = $composite RETURN constituents",
                composite=composite,
            )
            record = result.single()
    finally:
        driver.close()

    if record is None:
        click.echo(
            f"error: composite database '{composite}' not found after alias creation. "
            f"Ensure '{composite}' exists as a COMPOSITE DATABASE before registering constituents.",
            err=True,
        )
        sys.exit(1)

    constituents = record["constituents"] or []
    if alias not in constituents:
        click.echo(
            f"error: alias '{alias}' was not found in '{composite}'.constituents after creation. "
            f"Current constituents: {constituents}",
            err=True,
        )
        sys.exit(1)

    click.echo(
        f"registered: `{alias}` → database '{database}' "
        f"in composite '{composite}' ({len(constituents)} constituent(s) total)"
    )


# -- `mapforge unify` ------------------------------------------------------
# D4 (#77): cross-source SAME_AS candidates between two bundle snapshots.


@main.command("unify")
@click.argument("bundle_a", type=click.Path(exists=True))
@click.argument("bundle_b", type=click.Path(exists=True))
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Emit candidates as JSON instead of the summary.")
def unify_cmd(bundle_a: str, bundle_b: str, as_json: bool) -> None:
    """Propose cross-source SAME_AS links between two bundles (D4, #77).

    BUNDLE_A and BUNDLE_B are bundle directories (each with a bundle.ir.json
    snapshot) or snapshot files. Surfaces same-category nodes with different
    primary ids that share an identifier — candidates for a SAME_AS link.
    """
    from .unification import propose_same_as_dirs

    try:
        candidates = propose_same_as_dirs(bundle_a, bundle_b)
    except FileNotFoundError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(candidates, indent=2, default=str))
        return
    if not candidates:
        click.echo("no cross-source SAME_AS candidates")
        return
    click.echo(f"{len(candidates)} SAME_AS candidate(s):")
    for c in candidates:
        click.echo(
            f"  {c['label']}: {c['a_id']}  ==  {c['b_id']}  "
            f"(shared: {', '.join(c['shared'])})"
        )
