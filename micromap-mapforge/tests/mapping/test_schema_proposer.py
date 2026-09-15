"""LLM-assisted schema authoring — D2 (#77)."""
from unittest.mock import MagicMock

import pytest

from micromap_mapforge.inspect.types import ColumnProfile, SourceProfile
from micromap_mapforge.mapping.bioregistry_check import unknown_prefixes
from micromap_mapforge.mapping.schema_config import SchemaConfigError
from micromap_mapforge.mapping.schema_proposer import propose_schema

_GOOD_YAML = """
name: crispr-offtarget
description: CRISPR off-target effects in plant pathogens.
version: "1.0.0"
prefixes:
  HGNC: https://www.genenames.org/data/gene-symbol-report/
classes:
  Gene:
    id_prefixes: [HGNC]
    slots: [id, name]
    x_mapforge:
      primary_id: id
      identifiers: [id]
"""

_BOGUS_PREFIX_YAML = """
name: x
description: d
version: "1.0.0"
classes:
  Gene:
    id_prefixes: [FOOBAR123]
    slots: [id]
    x_mapforge: {primary_id: id, identifiers: [id]}
"""


def _profile():
    return SourceProfile(
        path="data.csv", format="csv", row_count_estimate=2,
        columns=[ColumnProfile("gene", "string", 0.0, 2, ["BRCA1", "TP53"])],
    )


def _fake_client(yaml_text):
    block = MagicMock()
    block.text = yaml_text
    resp = MagicMock()
    resp.content = [block]
    client = MagicMock()
    client.messages.create = MagicMock(return_value=resp)
    return client


def test_propose_schema_parses_and_returns_proposal():
    client = _fake_client(_GOOD_YAML)
    proposal = propose_schema(_profile(), hint="plant pathogens", client=client)
    assert list(proposal.schema_config["classes"]) == ["Gene"]
    assert proposal.rationale.startswith("CRISPR off-target")
    assert proposal.conflicts == []  # HGNC is a real Bioregistry prefix


def test_prompt_includes_profile_hint_and_base_template():
    client = _fake_client(_GOOD_YAML)
    base = {"name": "genomics", "classes": {"Gene": {"id_prefixes": ["HGNC"]}}}
    propose_schema(_profile(), hint="CRISPR off-target", base_template=base, client=client)
    kwargs = client.messages.create.call_args.kwargs
    # system carries the authoring prompt + the base-template cache block
    system_text = " ".join(b["text"] for b in kwargs["system"])
    assert "schema author" in system_text and "base_template" in system_text
    # user message carries the profile + hint
    user = kwargs["messages"][0]["content"]
    assert "source_profile" in user and "CRISPR off-target" in user
    assert kwargs["model"] == "claude-sonnet-4-6"


def test_surfaces_bioregistry_conflicts():
    proposal = propose_schema(_profile(), client=_fake_client(_BOGUS_PREFIX_YAML))
    prefixes = {c["prefix"] for c in proposal.conflicts}
    assert "FOOBAR123" in prefixes


def test_defaults_version_with_warning():
    yaml_no_version = "name: x\ndescription: d\nclasses:\n  Gene:\n    id_prefixes: [HGNC]\n"
    proposal = propose_schema(_profile(), client=_fake_client(yaml_no_version))
    assert proposal.schema_config["version"] == "1.0.0"
    assert any("version" in w for w in proposal.warnings)


def test_malformed_response_raises():
    with pytest.raises(SchemaConfigError):
        propose_schema(_profile(), client=_fake_client("just a string, no classes"))
    with pytest.raises(SchemaConfigError):
        propose_schema(_profile(), client=_fake_client("name: x\nclasses: {}\n"))


# --- the non-raising Bioregistry collector (reused by the proposer) ---

def test_unknown_prefixes_collects_all():
    schema = {
        "prefixes": {"NCBITaxon": "u", "BOGUSXYZ": "u"},
        "classes": {"Gene": {"id_prefixes": ["HGNC", "ALSO_BOGUS"]}},
    }
    found = {c["prefix"] for c in unknown_prefixes(schema)}
    assert found == {"BOGUSXYZ", "ALSO_BOGUS"}   # NCBITaxon + HGNC are valid


# --- CLI ---

def test_cli_propose_schema(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from micromap_mapforge import cli
    from micromap_mapforge.mapping import schema_proposer
    from micromap_mapforge.mapping.schema_proposer import SchemaProposal

    src = tmp_path / "data.csv"
    src.write_text("gene,score\nBRCA1,0.9\n", encoding="utf-8")

    canned = SchemaProposal(
        schema_config={"name": "x", "description": "why", "version": "1.0.0",
                       "classes": {"Gene": {"id_prefixes": ["HGNC"]}}},
        conflicts=[{"prefix": "ZZZ", "where": "class:Gene", "suggestion": None}],
        warnings=[],
    )
    monkeypatch.setattr(schema_proposer, "propose_schema", lambda *a, **k: canned)

    out = tmp_path / "schema.yaml"
    res = CliRunner().invoke(cli.main, ["propose-schema", str(src), "--out", str(out),
                                        "--hint", "x"])
    assert res.exit_code == 0, res.output
    assert out.is_file()
    body = out.read_text(encoding="utf-8")
    assert "classes:" in body and "Gene" in body
    assert "classes: Gene" in res.output and "rationale: why" in res.output
    assert "ZZZ" in res.output  # conflict surfaced
