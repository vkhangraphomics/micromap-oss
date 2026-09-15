"""License-aware import enforcement — C6.1 (#76)."""
from micromap_mapforge.catalog import KgSource
from micromap_mapforge.catalog.license_policy import (
    LicensePolicy,
    evaluate,
    load_policy,
)


def _src(commercial_use: str, *, license: str = "SOME", id: str = "k") -> KgSource:
    return KgSource(
        id=id, name=id.title(), homepage="h", description="d", license=license,
        commercial_use=commercial_use, license_notes="n", adapter_status="unknown",
        adapter_location="", biolink_categories=["Gene"], recommended_for=["x"],
        est_size="s", last_validated="2026-01-01",
    )


# --- evaluate ---

def test_non_commercial_permits_everything():
    pol = LicensePolicy(commercial=False)
    for cu in ("allowed", "restricted", "prohibited", "unknown"):
        assert evaluate(_src(cu), pol).allowed is True


def test_denylist_blocks_even_non_commercial():
    pol = LicensePolicy(commercial=False, denylist=frozenset({"k"}))
    assert evaluate(_src("allowed"), pol).allowed is False


def test_commercial_gates_by_posture():
    pol = LicensePolicy(commercial=True)
    assert evaluate(_src("allowed"), pol).allowed is True
    assert evaluate(_src("restricted"), pol).allowed is False
    assert evaluate(_src("prohibited"), pol).allowed is False
    assert evaluate(_src("unknown"), pol).allowed is False


def test_commercial_opt_ins():
    assert evaluate(_src("restricted"), LicensePolicy(commercial=True, allow_restricted=True)).allowed
    assert evaluate(_src("unknown"), LicensePolicy(commercial=True, allow_unknown=True)).allowed
    # prohibited is never opened by allow_restricted/allow_unknown
    assert not evaluate(_src("prohibited"), LicensePolicy(commercial=True, allow_restricted=True)).allowed


def test_commercial_allowlist_overrides_posture():
    pol = LicensePolicy(commercial=True, allowlist=frozenset({"PAID-OK"}))
    assert evaluate(_src("prohibited", license="PAID-OK"), pol).allowed is True


def test_load_policy(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "commercial: true\nallow_restricted: true\nallowlist: [CC-BY-4.0]\ndenylist: [kegg]\n",
        encoding="utf-8",
    )
    pol = load_policy(p)
    assert pol.commercial and pol.allow_restricted
    assert "CC-BY-4.0" in pol.allowlist and "kegg" in pol.denylist


# --- CLI: sources check ---

def _run(args):
    from click.testing import CliRunner

    from micromap_mapforge.cli import main
    return CliRunner().invoke(main, args)


def test_check_default_non_commercial_allows():
    res = _run(["sources", "check", "drugbank"])  # academic default
    assert res.exit_code == 0 and "ALLOWED" in res.output


def test_check_commercial_refuses_drugbank():
    res = _run(["sources", "check", "drugbank", "--commercial"])
    assert res.exit_code == 3 and "REFUSED" in res.output and "paid license" in res.output


def test_check_commercial_allows_cc_by():
    res = _run(["sources", "check", "reactome", "--commercial"])
    assert res.exit_code == 0 and "ALLOWED" in res.output


def test_check_commercial_restricted_then_opt_in():
    assert _run(["sources", "check", "hmdb", "--commercial"]).exit_code == 3
    assert _run(["sources", "check", "hmdb", "--commercial", "--allow-restricted"]).exit_code == 0


def test_check_unknown_id_exit_2():
    res = _run(["sources", "check", "nope"])
    assert res.exit_code == 2


# --- CLI: import-kg gate ---

def test_import_kg_refused_by_license_gate(tmp_path):
    src = tmp_path / "data.tsv"
    src.write_text("a\tb\n1\t2\n", encoding="utf-8")
    res = _run([
        "import-kg", "bogus.module:Adapter", "--source", str(src),
        "--organization-id", "acme", "--out", str(tmp_path / "out"),
        "--from", "drugbank", "--commercial",
    ])
    assert res.exit_code == 3
    assert "refused" in res.output.lower() and "paid license" in res.output
    assert not (tmp_path / "out").exists()  # never got to writing a bundle
