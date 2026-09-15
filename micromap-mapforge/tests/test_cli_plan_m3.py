# micromap-mapforge/tests/test_cli_plan_m3.py
from pathlib import Path

import yaml
from click.testing import CliRunner

from micromap_mapforge.cli import main


def _seed_bundle(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    (out / "mapping.yaml").write_text(
        yaml.safe_dump({"source": {"name": "s", "format": "tsv", "path": "s.tsv"},
                        "entities": [], "relationships": []}),
        encoding="utf-8",
    )
    return out


def _seed_policy(tmp_path: Path) -> Path:
    policy = {
        "rules": [
            {"match": {"tier": "internal"}, "destination": "micromap-core"},
            {"match": {"tier": "external"}, "destination": "registry-only"},
        ],
        "default": {"destination": "registry-only"},
    }
    p = tmp_path / "policy.yaml"
    p.write_text(yaml.safe_dump(policy), encoding="utf-8")
    return p


def test_plan_with_policy_and_contributor_yaml(tmp_path):
    out = _seed_bundle(tmp_path)
    (out / "contributor.yaml").write_text(
        yaml.safe_dump({"contributor": "acme", "tier": "internal"}),
        encoding="utf-8",
    )
    policy = _seed_policy(tmp_path)

    runner = CliRunner()
    res = runner.invoke(main, [
        "plan", "--bundle", str(out), "--organization-id", "acme",
        "--policy", str(policy),
    ])
    assert res.exit_code == 0, res.output
    routing = yaml.safe_load((out / "routing.yaml").read_text(encoding="utf-8"))
    assert routing["destination"] == "micromap-core"


def test_plan_cli_flags_override_contributor_yaml(tmp_path):
    out = _seed_bundle(tmp_path)
    (out / "contributor.yaml").write_text(
        yaml.safe_dump({"contributor": "acme", "tier": "internal"}),
        encoding="utf-8",
    )
    policy = _seed_policy(tmp_path)

    runner = CliRunner()
    res = runner.invoke(main, [
        "plan", "--bundle", str(out), "--organization-id", "acme",
        "--policy", str(policy),
        "--tier", "external",
    ])
    assert res.exit_code == 0, res.output
    routing = yaml.safe_load((out / "routing.yaml").read_text(encoding="utf-8"))
    assert routing["destination"] == "registry-only"


def test_plan_without_policy_falls_back_to_core(tmp_path):
    out = _seed_bundle(tmp_path)
    runner = CliRunner()
    res = runner.invoke(main, [
        "plan", "--bundle", str(out), "--organization-id", "acme",
    ])
    assert res.exit_code == 0, res.output
    routing = yaml.safe_load((out / "routing.yaml").read_text(encoding="utf-8"))
    assert routing["destination"] == "micromap-core"
