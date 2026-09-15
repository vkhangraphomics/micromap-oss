from __future__ import annotations

import gzip
import os
from unittest.mock import MagicMock


from database.ingestion.phosphosite_loader import PhosphoSiteLoader

SAMPLE_TSV_CONTENT = """\
# PhosphoSitePlus (R), www.phosphosite.org
# Copyright (C) 2004-2024 Cell Signaling Technology, Inc.
#
GENE\tPROTEIN\tACC_ID\tMOD_RSD\tORGANISM\tKINASE\tLT_LIT
TP53\tp53\tP04637\tS15-p\thuman\tATM\t47
TP53\tp53\tP04637\tS20-p\thuman\tCHK2\t23
EGFR\tEGFR\tP00533\tY1068-p\thuman\t\t12
MOUSE\tMouseP\tQ12345\tS10-p\tmouse\t\t5
TP53\tp53\tP04637\t-p\thuman\tATM\t1
"""


def _make_loader(file_path: str = os.devnull) -> PhosphoSiteLoader:
    loader = object.__new__(PhosphoSiteLoader)
    loader.organization_id = "test-org"
    loader.driver = MagicMock()
    loader.database = "neo4j"
    loader.file_path = file_path
    loader.data_dir = None
    loader.stats = MagicMock()
    return loader


def test_extracts_human_only(tmp_path):
    tsv_file = tmp_path / "Phosphorylation_site_dataset"
    tsv_file.write_text(SAMPLE_TSV_CONTENT, encoding="utf-8")

    mock_driver = MagicMock()
    loader = PhosphoSiteLoader(
        driver=mock_driver,
        organization_id="test-org",
        file_path=str(tsv_file),
        batch_size=1000,
        database="neo4j",
    )

    records = list(loader.extract())
    acc_ids = [r["ACC_ID"] for r in records]

    assert "P04637" in acc_ids, "Human TP53 (P04637) should be extracted"
    assert "P00533" in acc_ids, "Human EGFR (P00533) should be extracted"
    assert "Q12345" not in acc_ids, "Mouse protein (Q12345) should be filtered out"


def test_transform_builds_modification_id(tmp_path):
    loader = _make_loader()
    raw = {
        "GENE": "TP53",
        "PROTEIN": "p53",
        "ACC_ID": "P04637",
        "MOD_RSD": "S15-p",
        "ORGANISM": "human",
        "KINASE": "ATM",
        "LT_LIT": "47",
    }
    result = loader.transform(raw)
    assert result is not None
    assert result["modification_id"] == "P04637:phosphorylation:S15"
    assert result["site"] == "S15"
    assert result["ptm_type"] == "phosphorylation"
    assert result["enzyme"] == "ATM"
    assert result["evidence_count"] == 47


def test_transform_skips_missing_site():
    loader = _make_loader()
    raw = {
        "GENE": "TP53",
        "PROTEIN": "p53",
        "ACC_ID": "P04637",
        "MOD_RSD": "-p",
        "ORGANISM": "human",
        "KINASE": "ATM",
        "LT_LIT": "1",
    }
    result = loader.transform(raw)
    assert result is None


def test_transform_skips_missing_accession():
    loader = _make_loader()
    raw = {
        "GENE": "TP53",
        "PROTEIN": "p53",
        "ACC_ID": "",
        "MOD_RSD": "S15-p",
        "ORGANISM": "human",
        "KINASE": "ATM",
        "LT_LIT": "10",
    }
    result = loader.transform(raw)
    assert result is None


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
            "modification_id": "P04637:phosphorylation:S15",
            "uniprot_accession": "P04637",
            "ptm_type": "phosphorylation",
            "site": "S15",
            "gene": "TP53",
            "protein_name": "p53",
            "enzyme": "ATM",
            "evidence_count": 47,
            "source": "phosphosite",
            "sources": ["phosphosite"],
            "organization_id": "test-org",
        }
    ]
    result = loader.load_batch(batch)
    assert isinstance(result, dict)
    assert "nodes_created" in result
    assert "relationships_created" in result


def test_extracts_human_only_gzip(tmp_path):
    gz_file = tmp_path / "Phosphorylation_site_dataset.gz"
    with gzip.open(str(gz_file), "wt", encoding="utf-8") as fh:
        fh.write(SAMPLE_TSV_CONTENT)

    mock_driver = MagicMock()
    loader = PhosphoSiteLoader(
        driver=mock_driver,
        organization_id="test-org",
        file_path=str(gz_file),
        batch_size=1000,
        database="neo4j",
    )

    records = list(loader.extract())
    acc_ids = [r["ACC_ID"] for r in records]

    assert "P04637" in acc_ids, "Human TP53 (P04637) should be extracted from gzip"
    assert "P00533" in acc_ids, "Human EGFR (P00533) should be extracted from gzip"
    assert "Q12345" not in acc_ids, "Mouse protein (Q12345) should be filtered out from gzip"
