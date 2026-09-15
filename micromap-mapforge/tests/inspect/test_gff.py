"""#286 G5: GFF3/GTF + BED annotation inspectors (genomics).

Fixed-column tab-delimited text — native, no dependency. GFF3/GTF's 9th column is
a structured attribute string (`gene_id=..;gene_name=..` or `gene_id ".."; ...`);
the inspector EXTRACTS the attribute keys into columns so gene_id/transcript_id
map straight onto the genomics Gene/Transcript. BED is positional (chrom/start/end
[/name/score/strand]).
"""
from pathlib import Path

from micromap_mapforge.inspect.gff import inspect_gff, inspect_bed
from micromap_mapforge.inspect.dispatch import inspect

_GFF3 = (
    "##gff-version 3\n"
    "##sequence-region chr1 1 248956422\n"
    "chr1\tHAVANA\tgene\t11869\t14409\t.\t+\t.\tID=gene:ENSG001;gene_id=ENSG001;gene_name=DDX11L1;biotype=lncRNA\n"
    "chr1\tHAVANA\ttranscript\t11869\t14409\t.\t+\t.\tID=tx:ENST001;gene_id=ENSG001;transcript_id=ENST001\n"
    "chr1\tHAVANA\texon\t11869\t12227\t.\t+\t.\tgene_id=ENSG001;transcript_id=ENST001;exon_number=1\n"
)

_GTF = (
    'chr1\tHAVANA\tgene\t11869\t14409\t.\t+\t.\tgene_id "ENSG001"; gene_name "DDX11L1"; biotype "lncRNA";\n'
    'chr1\tHAVANA\ttranscript\t11869\t14409\t.\t+\t.\tgene_id "ENSG001"; transcript_id "ENST001";\n'
)

_BED = (
    "chr1\t11868\t14409\tDDX11L1\t0\t+\n"
    "chr1\t14403\t29570\tWASH7P\t0\t-\n"
)


def _write(tmp_path, text, name):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _col(prof, name):
    return next(c for c in prof.columns if c.name == name)


def test_gff3_profiles_fixed_columns_and_extracts_attributes(tmp_path: Path):
    prof = inspect_gff(_write(tmp_path, _GFF3, "ann.gff3"))
    assert prof.format == "gff3"
    names = [c.name for c in prof.columns]
    for fixed in ["seqid", "type", "start", "end", "strand"]:
        assert fixed in names
    # attribute keys are exploded into columns
    for attr in ["gene_id", "gene_name", "transcript_id"]:
        assert attr in names
    assert _col(prof, "start").inferred_type == "integer"
    assert prof.row_count_estimate == 3
    assert "gene" in prof.note   # feature-type inventory in the note


def test_gtf_quoted_attributes_are_extracted(tmp_path: Path):
    prof = inspect_gff(_write(tmp_path, _GTF, "ann.gtf"))
    assert prof.format == "gtf"
    names = [c.name for c in prof.columns]
    assert "gene_id" in names and "transcript_id" in names
    gid = _col(prof, "gene_id")
    assert "ENSG001" in gid.samples          # value unquoted, not '"ENSG001"'


def test_bed_positional_columns(tmp_path: Path):
    prof = inspect_bed(_write(tmp_path, _BED, "regions.bed"))
    assert prof.format == "bed"
    names = [c.name for c in prof.columns]
    assert names[:4] == ["chrom", "start", "end", "name"]
    assert _col(prof, "start").inferred_type == "integer"
    assert prof.row_count_estimate == 2


def test_dispatch_routes_gff_gtf_bed(tmp_path: Path):
    assert inspect(_write(tmp_path, _GFF3, "a.gff3")).format == "gff3"
    assert inspect(_write(tmp_path, _GFF3, "a.gff")).format == "gff3"
    assert inspect(_write(tmp_path, _GTF, "a.gtf")).format == "gtf"
    assert inspect(_write(tmp_path, _BED, "a.bed")).format == "bed"


def test_gff_maps_onto_genomics_gene(tmp_path: Path):
    from micromap_mapforge.mapping.mapper import draft_heuristic_mapping
    from micromap_mapforge.mapping.schema_config import load_schema_config

    prof = inspect_gff(_write(tmp_path, _GFF3, "ann.gff3"))
    mapping = draft_heuristic_mapping(prof, load_schema_config("genomics"))
    labels = [e["label"] for e in mapping.get("entities", [])]
    assert "Gene" in labels
