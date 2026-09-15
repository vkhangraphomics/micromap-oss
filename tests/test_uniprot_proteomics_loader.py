from __future__ import annotations

from unittest.mock import MagicMock


from database.ingestion.uniprot_proteomics_loader import UniProtProteomicsLoader

SAMPLE_DAT = b"""\
ID   TP53_HUMAN              Reviewed;         393 AA.
AC   P04637; Q15086;
DE   RecName: Full=Cellular tumor antigen p53;
GN   Name=TP53;
OS   Homo sapiens (Human).
DR   Reactome; R-HSA-5633007; Regulation of TP53 Degradation.
DR   MIM; 191170; phenotype.
FT   MOD_RES         15
FT                   /note="Phosphoserine; by AURORA-A"
FT                   /evidence="ECO:0000269|PubMed:17401432"
SQ   SEQUENCE   393 AA;
//
ID   EGFR_HUMAN              Reviewed;        1210 AA.
AC   P00533;
DE   RecName: Full=Epidermal growth factor receptor;
GN   Name=EGFR;
OS   Homo sapiens (Human).
DR   Reactome; R-HSA-177929; Signaling by EGFR.
SQ   SEQUENCE  1210 AA;
//
ID   MOUSE_PROT              Reviewed;         200 AA.
AC   Q12345;
DE   RecName: Full=Some mouse protein;
GN   Name=MOUSEGN;
OS   Mus musculus (Mouse).
SQ   SEQUENCE   200 AA;
//
"""


def _make_loader() -> UniProtProteomicsLoader:
    loader = object.__new__(UniProtProteomicsLoader)
    loader.organization_id = "test-org"
    loader.driver = MagicMock()
    loader.database = "neo4j"
    loader.stats = MagicMock()
    return loader


def test_extracts_human_only(tmp_path):
    dat_file = tmp_path / "uniprot_sprot.dat"
    dat_file.write_bytes(SAMPLE_DAT)
    loader = _make_loader()
    loader.file_path = str(dat_file)
    records = list(loader.extract())
    accs = [r["accession"] for r in records]
    assert "P04637" in accs
    assert "P00533" in accs
    assert "Q12345" not in accs, "Mouse protein should be filtered out"
    # At least one record must have PTM features parsed (catches bug 1 regression)
    assert any(r["ptm_features"] for r in records), (
        "Expected at least one record to have ptm_features — FT /note= parsing may be broken"
    )


def test_transform_produces_protein_node():
    loader = _make_loader()
    raw = {
        "accession": "P04637",
        "name": "Cellular tumor antigen p53",
        "gene_name": "TP53",
        "organism": "Homo sapiens (Human).",
        "reactome_ids": ["R-HSA-5633007"],
        "mim_phenotypes": ["191170"],
        "ptm_features": [{"position": "15", "note": "Phosphoserine; by AURORA-A"}],
    }
    result = loader.transform(raw)
    assert result is not None
    assert result["protein_id"] == "UNIPROT:P04637"
    assert result["gene_name"] == "TP53"


def test_transform_skips_record_without_accession():
    loader = _make_loader()
    raw = {
        "accession": None,
        "name": "Unknown",
        "gene_name": "",
        "organism": "Homo sapiens",
        "reactome_ids": [],
        "mim_phenotypes": [],
        "ptm_features": [],
    }
    result = loader.transform(raw)
    assert result is None


def test_transform_builds_modification_entries():
    loader = _make_loader()
    raw = {
        "accession": "P04637",
        "name": "Cellular tumor antigen p53",
        "gene_name": "TP53",
        "organism": "Homo sapiens (Human).",
        "reactome_ids": [],
        "mim_phenotypes": [],
        "ptm_features": [
            {"position": "15", "note": "Phosphoserine; by AURORA-A"},
            {"position": "20", "note": "Phosphothreonine; by ATM"},
        ],
    }
    result = loader.transform(raw)
    mods = result["_modifications"]
    assert len(mods) == 2
    assert mods[0]["modification_id"] == "P04637:phosphorylation:S15"
    assert mods[0]["ptm_type"] == "phosphorylation"
    assert mods[0]["site"] == "S15"


def test_transform_skips_modification_missing_site():
    loader = _make_loader()
    raw = {
        "accession": "P04637",
        "name": "p53",
        "gene_name": "TP53",
        "organism": "Homo sapiens",
        "reactome_ids": [],
        "mim_phenotypes": [],
        "ptm_features": [{"position": None, "note": "Phosphoserine; by ATM"}],
    }
    result = loader.transform(raw)
    assert result["_modifications"] == []


def test_load_batch_returns_dict():
    loader = _make_loader()
    session = MagicMock()
    session.run = MagicMock(
        return_value=MagicMock(single=MagicMock(return_value={"count": 1}))
    )
    loader.driver.session = MagicMock(
        return_value=MagicMock(
            __enter__=MagicMock(return_value=session),
            __exit__=MagicMock(return_value=False),
        )
    )
    batch = [
        {
            "protein_id": "UNIPROT:P04637",
            "uniprot_accession": "P04637",
            "name": "Cellular tumor antigen p53",
            "gene_name": "TP53",
            "source": "uniprot",
            "sources": ["uniprot"],
            "organization_id": "test-org",
            "_reactome_ids": ["R-HSA-5633007"],
            "_mim_phenotypes": ["191170"],
            "_modifications": [],
        }
    ]
    result = loader.load_batch(batch)
    assert isinstance(result, dict)
    assert "nodes_created" in result
    assert "relationships_created" in result
    # Confirm session.run was called for the Protein write + Pathway stub + Disease stub
    assert session.run.call_count >= 3


def test_real_constructor_extract_parses_ptm_features(tmp_path):
    """Integration test: use the real FileBasedLoader constructor (not object.__new__)
    and call extract() on the SAMPLE_DAT fixture.

    Catches bug 1 (FT /note= strip) and bug 2 (self.file_path attribute name).
    """
    dat_file = tmp_path / "uniprot_sprot.dat"
    dat_file.write_bytes(SAMPLE_DAT)

    mock_driver = MagicMock()
    # FileBasedLoader.__init__(driver, organization_id, file_path, batch_size, database)
    loader = UniProtProteomicsLoader(
        driver=mock_driver,
        organization_id="test-org",
        file_path=str(dat_file),
        batch_size=1000,
        database="neo4j",
    )

    records = list(loader.extract())

    # Only human records should be returned
    accs = [r["accession"] for r in records]
    assert "P04637" in accs, "TP53 (P04637) should be extracted"
    assert "Q12345" not in accs, "Mouse protein should be filtered out"

    # TP53 record must have PTM features parsed (Phosphoserine at position 15)
    tp53_records = [r for r in records if r["accession"] == "P04637"]
    assert tp53_records, "No TP53 record found"
    tp53 = tp53_records[0]
    assert len(tp53["ptm_features"]) > 0, (
        "TP53 should have at least one ptm_feature — "
        "FT /note= detection or file_path attribute may be broken"
    )
    assert tp53["ptm_features"][0]["position"] == "15"
    assert "Phosphoserine" in tp53["ptm_features"][0]["note"]


def test_load_batch_passes_organization_id_to_pathway_and_disease_stubs():
    """organization_id must be forwarded to Pathway and Disease stub MERGE params."""
    loader = _make_loader()
    session = MagicMock()
    session.run = MagicMock(
        return_value=MagicMock(single=MagicMock(return_value={"count": 1}))
    )
    loader.driver.session = MagicMock(
        return_value=MagicMock(
            __enter__=MagicMock(return_value=session),
            __exit__=MagicMock(return_value=False),
        )
    )
    batch = [
        {
            "protein_id": "UNIPROT:P04637",
            "uniprot_accession": "P04637",
            "name": "Cellular tumor antigen p53",
            "gene_name": "TP53",
            "source": "uniprot",
            "sources": ["uniprot"],
            "organization_id": "test-org",
            "_reactome_ids": ["R-HSA-5633007"],
            "_mim_phenotypes": ["191170"],
            "_modifications": [],
        }
    ]
    loader.load_batch(batch)

    # Collect all kwargs passed to session.run
    all_params = [call.args[1] for call in session.run.call_args_list if len(call.args) >= 2]

    # The Pathway stub call must include organization_id
    pathway_params = [p for p in all_params if p.get("pathway_id") == "REACTOME:R-HSA-5633007"]
    assert pathway_params, "Expected a session.run call for the Pathway stub"
    assert pathway_params[0]["organization_id"] == "test-org", (
        "Pathway stub MERGE must receive organization_id"
    )

    # The Disease stub call must include organization_id
    disease_params = [p for p in all_params if p.get("disease_id") == "OMIM:191170"]
    assert disease_params, "Expected a session.run call for the Disease stub"
    assert disease_params[0]["organization_id"] == "test-org", (
        "Disease stub MERGE must receive organization_id"
    )
