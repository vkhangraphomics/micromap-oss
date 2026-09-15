"""#286 G1: VCF variant-call inspector (genomics).

Plain-text VCF is tab-delimited with `##` header lines — parsed natively (no
dependency). The `##INFO`/`##FORMAT` declarations are a typed column schema, so
POS/QUAL get their real types (integer/float) rather than CSV inference. `.vcf.gz`
(bgzf is gzip-readable) flows through G0 automatically; binary `.bcf` is rejected
with an actionable convert step.

CHROM/POS/ID/REF/ALT -> Variant; per-sample genotype columns -> Sample.
"""
import gzip
from pathlib import Path

import pytest

from micromap_mapforge.inspect.vcf import inspect_vcf
from micromap_mapforge.inspect.dispatch import inspect

_VCF = (
    "##fileformat=VCFv4.2\n"
    "##INFO=<ID=AF,Number=A,Type=Float,Description=\"Allele Frequency\">\n"
    "##INFO=<ID=DP,Number=1,Type=Integer,Description=\"Total Depth\">\n"
    "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
    "##FORMAT=<ID=DP,Number=1,Type=Integer,Description=\"Read Depth\">\n"
    "##contig=<ID=chr1,length=248956422>\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNA001\tNA002\n"
    "chr1\t12345\trs123\tA\tG\t50\tPASS\tAF=0.3;DP=100\tGT:DP\t0/1:50\t1/1:45\n"
    "chr1\t23456\t.\tC\tT\t99\tPASS\tAF=0.1;DP=80\tGT:DP\t0/0:40\t0/1:38\n"
    "chr2\t34567\trs789\tG\tA\t30\tq10\tAF=0.5;DP=60\tGT:DP\t1/1:30\t./.:0\n"
)


def _write(tmp_path: Path, text: str = _VCF, name: str = "variants.vcf") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _col(prof, name):
    return next(c for c in prof.columns if c.name == name)


def test_vcf_profiles_fixed_columns_with_header_derived_types(tmp_path: Path):
    prof = inspect_vcf(_write(tmp_path))
    assert prof.format == "vcf"
    names = [c.name for c in prof.columns]
    for fixed in ["CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO"]:
        assert fixed in names
    assert "NA001" in names and "NA002" in names        # per-sample columns
    assert prof.row_count_estimate == 3                  # 3 variants
    # header-declared types, not CSV inference
    assert _col(prof, "POS").inferred_type == "integer"
    assert _col(prof, "QUAL").inferred_type == "float"


def test_vcf_note_reports_variants_samples_and_info_format_fields(tmp_path: Path):
    prof = inspect_vcf(_write(tmp_path))
    assert "3 variants" in prof.note and "2 samples" in prof.note
    assert "AF" in prof.note and "DP" in prof.note       # INFO/FORMAT field inventory


def test_dispatch_routes_dot_vcf(tmp_path: Path):
    assert inspect(_write(tmp_path)).format == "vcf"


def test_vcf_gz_flows_through_g0_decompression(tmp_path: Path):
    gz = tmp_path / "variants.vcf.gz"
    gz.write_bytes(gzip.compress(_VCF.encode("utf-8")))
    assert inspect(gz).format == "vcf"                   # G0 -> inner .vcf -> inspect_vcf


def test_binary_bcf_gets_actionable_error(tmp_path: Path):
    p = tmp_path / "variants.bcf"
    p.write_bytes(b"BCF\x02\x02" + b"\x00" * 16)         # BCF magic
    with pytest.raises(ValueError, match="(?i)bcftools|text|vcf|omics") as ei:
        inspect_vcf(p)
    assert "unsupported file extension" not in str(ei.value)


def test_vcf_maps_onto_the_genomics_variant(tmp_path: Path):
    # #286 acceptance: inspect -> map against the matching discipline template.
    from micromap_mapforge.mapping.mapper import draft_heuristic_mapping
    from micromap_mapforge.mapping.schema_config import load_schema_config

    prof = inspect_vcf(_write(tmp_path))
    mapping = draft_heuristic_mapping(prof, load_schema_config("genomics"))
    labels = [e["label"] for e in mapping.get("entities", [])]
    assert "Variant" in labels
