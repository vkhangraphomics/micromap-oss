"""Route a source path to the right inspector by file extension."""

import bz2
import gzip
import lzma
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from .archive import ARCHIVE_SUFFIXES, inspect_archive, is_archive
from .biom import inspect_biom
from .csv_tsv import inspect_csv, inspect_tsv
from .gff import inspect_bed, inspect_gff
from .h5ad import inspect_anndata
from .json_files import inspect_json, inspect_jsonl
from .mztab import inspect_mztab
from .parquet import inspect_parquet
from .pdf import inspect_pdf
from .raw import RAW_FORMATS, is_raw_source, raw_provenance_profile
from .sql_dump import inspect_sql_dump
from .tenx import inspect_10x, is_10x_dir
from .types import SourceProfile
from .vcf import inspect_vcf
from .xlsx import inspect_xlsx

# #286 G0: single-file compression (NOT tar — those are archives). A bare
# `.csv.gz` is decompressed and re-dispatched on the inner suffix.
_COMPRESSION = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}

_DISPATCH = {
    ".csv": inspect_csv,
    ".tsv": inspect_tsv,
    ".json": inspect_json,
    ".jsonl": inspect_jsonl,
    ".ndjson": inspect_jsonl,
    ".parquet": inspect_parquet,
    ".sql": inspect_sql_dump,
    ".biom": inspect_biom,   # #286 G2: BIOM-1.0 JSON native; HDF5 -> convert error
    ".vcf": inspect_vcf,     # #286 G1: text VCF native (.vcf.gz via G0); BCF -> convert error
    ".gff": inspect_gff,     # #286 G5: GFF3/GTF annotation (attribute extraction)
    ".gff3": inspect_gff,
    ".gtf": inspect_gff,
    ".bed": inspect_bed,     # #286 G5: BED positional intervals
    ".h5ad": inspect_anndata,  # #286 G3: AnnData (needs [omics] extra)
    ".loom": inspect_anndata,
}

# Workbook formats route to the xlsx inspector, which takes an optional `sheet`.
_XLSX_EXTS = {".xlsx", ".xlsm"}

# PDF routes to the pdf inspector, which takes an optional 1-based `table`.
_PDF_EXTS = {".pdf"}

# #286 G4/G4b: mzTab takes an optional `section` (like xlsx→sheet), so it's not
# in the option-less _DISPATCH map.
_MZTAB_EXTS = {".mztab"}


def supported_extensions() -> set[str]:
    """Single-file extensions the inspector can profile (archives expand to these)."""
    return set(_DISPATCH) | _XLSX_EXTS | _PDF_EXTS | _MZTAB_EXTS


def inspect(path: str | Path, sheet: str | None = None,
            member: str | None = None, table: int | None = None,
            section: str | None = None, as_source: bool = False) -> SourceProfile:
    """Profile a source. ``section`` selects an mzTab table (#286 G4b);
    ``as_source`` opts a raw reads/spectra file into a provenance-only stub
    instead of the default actionable rejection (#286 G8b) — a no-op for
    non-raw formats."""
    p = Path(path)
    if p.is_dir():
        # #286 G3b: the only directory input is a 10x matrix (matrix.mtx +
        # features.tsv + barcodes.tsv). Anything else is an explicit error.
        if is_10x_dir(p):
            return inspect_10x(p)
        raise ValueError(
            f"{p}: directory input is only supported for a 10x matrix "
            f"(matrix.mtx[.gz] + features.tsv[.gz] + barcodes.tsv[.gz]); "
            f"none found here."
        )
    # #286 G8b: a raw file referenced as a source gets a provenance stub — this
    # sees through .gz (reads.fastq.gz) and hashes the file on disk. Only when
    # opted in; the default path still reaches the actionable error below.
    if as_source and is_raw_source(p):
        return raw_provenance_profile(p)
    if is_archive(p):
        return inspect_archive(p, member=member, sheet=sheet, table=table)
    ext = p.suffix.lower()
    if ext in _COMPRESSION:
        return _inspect_compressed(p, ext, sheet=sheet, member=member,
                                   table=table, section=section, as_source=as_source)
    if ext in _XLSX_EXTS:
        return inspect_xlsx(p, sheet=sheet)
    if ext in _PDF_EXTS:
        return inspect_pdf(p, table=table)
    if ext in _MZTAB_EXTS:
        return inspect_mztab(p, section=section)
    fn = _DISPATCH.get(ext)
    if fn is not None:
        return fn(p)
    raise _unsupported_error(p, ext)


def _inspect_compressed(p: Path, ext: str, **opts) -> SourceProfile:
    """G0: decompress a single-file .gz/.bz2/.xz to a temp file named by the inner
    suffix, then re-dispatch through inspect() (so the inner suffix gets the full
    treatment — real inspector, or G8a's actionable error for a raw format)."""
    inner_suffix = Path(p.stem).suffix.lower()   # "a.csv.gz" -> stem "a.csv" -> ".csv"
    if not inner_suffix:
        raise ValueError(
            f"{p}: '{ext}' file has no inner extension to profile — expected e.g. "
            f"'name.csv{ext}'. Rename it with the inner format's extension."
        )
    opener = _COMPRESSION[ext]
    with opener(p, "rb") as src, tempfile.NamedTemporaryFile(
            suffix=inner_suffix, delete=False) as tmp:
        shutil.copyfileobj(src, tmp)
        tmp_path = Path(tmp.name)
    try:
        inner = inspect(tmp_path, **opts)
    finally:
        tmp_path.unlink(missing_ok=True)
    note = f"decompressed from single-file {ext}"
    if inner.note:
        note = f"{note} | {inner.note}"
    return replace(inner, path=str(p), note=note)


def _unsupported_error(p: Path, ext: str) -> ValueError:
    """G8a: a recognized raw read/spectra format gets an actionable next step;
    anything else gets the generic supported-extension list."""
    kind = RAW_FORMATS.get(ext)
    if kind is not None:
        return ValueError(
            f"{p}: '{ext}' is {kind} — it has no column/matrix structure or "
            f"KG-mappable entities until a pipeline processes it. MapForge ingests "
            f"tabular/matrix artifacts (count tables, feature tables, variant "
            f"tables, quantification matrices), not raw reads/spectra. Run it "
            f"through a Workbench pipeline (e.g. taxonomic profiling, "
            f"quantification, or variant calling) to produce a mappable artifact, "
            f"then ingest that. To reference it as a source without parsing, "
            f"pass --as-source (mapforge inspect) for a provenance-only stub."
        )
    return ValueError(
        f"{p}: unsupported file extension '{ext}'. "
        f"Supported: {sorted(supported_extensions())} "
        f"(+ archives: {sorted(ARCHIVE_SUFFIXES)}; single-file "
        f"{sorted(_COMPRESSION)} decompressed transparently)"
    )
