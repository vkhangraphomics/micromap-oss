"""Tests for the MCP tool layer in micromap_mcp/tools/mapforge.py.

Each test isolates the tool-layer logic by supplying a MagicMock runner so no
real Neo4j connection or file-system pipeline is required.
"""
from __future__ import annotations

import inspect
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from micromap_mcp.auth import Principal
from micromap_mcp.tools.mapforge import register_mapforge_tools


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def tools(tmp_path, env):
    """Return (callables_dict, mock_runner) with a mock runner wired up."""
    runner = MagicMock()
    runner.inspect.return_value = {"format": "tsv", "columns": []}
    runner.draft_heuristic.return_value = "source:\n  format: tsv\n"
    runner.resolve.return_value = {
        "resolved_count": 12,
        "unresolved_count": 0,
        "ambiguous_count": 0,
    }
    runner.plan.return_value = {
        "destination": "micromap-core",
        "organization_id": "default",
    }
    runner.emit.return_value = {
        "cypher_files": ["nodes_Taxon.cypher"],
        "bundle_dir": str(tmp_path),
    }
    runner.approve.return_value = {
        "reviewer": "qa",
        "approved_at": "2026-05-28T00:00:00Z",
    }
    runner.submit.return_value = {
        "success": True,
        "nodes_written": 10,
        "rels_written": 5,
    }

    callables = register_mapforge_tools(
        app=None,
        runner=runner,
        neo4j_uri="bolt://neo4j:7687",
        neo4j_user="neo4j",
        neo4j_password="pw",
        neo4j_database="neo4j",
        bundle_base=str(tmp_path),
        return_callables=True,
    )
    return callables, runner


# ---------------------------------------------------------------------------
# Tests — all async so pytest-asyncio (mode=AUTO) runs them in an event loop
# ---------------------------------------------------------------------------

async def test_mapforge_inspect_proxies_to_runner(tools, tmp_path):
    callables, runner = tools
    src = tmp_path / "x.tsv"
    src.write_text("a\tb\n1\t2\n", encoding="utf-8")
    out = await callables["mapforge_inspect"](source_path=str(src))
    # Multi-source options default through (sheet/member/table/section/as_source).
    runner.inspect.assert_called_once_with(
        str(src), sheet=None, member=None, table=None, section=None, as_source=False)
    assert out["format"] == "tsv"


async def test_mapforge_inspect_forwards_multi_source_options(tools, tmp_path):
    # #286: the MCP tool now exposes the same selectors as the CLI `inspect`
    # (--sheet/--member/--table/--section/--as-source) and forwards them.
    callables, runner = tools
    src = tmp_path / "s.mztab"
    src.write_text("MTD\tmzTab-version\t2.0.0-M\n", encoding="utf-8")
    await callables["mapforge_inspect"](
        source_path=str(src), sheet="S2", member="m.csv", table=3,
        section="PRT", as_source=True)
    runner.inspect.assert_called_once_with(
        str(src), sheet="S2", member="m.csv", table=3,
        section="PRT", as_source=True)


async def test_mapforge_create_bundle_returns_new_dir(tools, tmp_path):
    callables, _ = tools
    out = await callables["mapforge_create_bundle"]()
    assert "bundle_dir" in out
    assert out["bundle_dir"].startswith(str(tmp_path))
    assert Path(out["bundle_dir"]).is_dir()


async def test_mapforge_map_heuristic_returns_yaml(tools, tmp_path):
    callables, runner = tools
    src = tmp_path / "x.tsv"
    src.write_text("a\tb\n1\t2\n", encoding="utf-8")
    out = await callables["mapforge_map_heuristic"](source_path=str(src))
    runner.draft_heuristic.assert_called_once_with(str(src), schema_config=None)
    assert "mapping_yaml" in out


async def test_mapforge_resolve_passes_neo4j_creds(tools):
    callables, runner = tools
    bd = await callables["mapforge_create_bundle"]()
    await callables["mapforge_resolve"](bundle_dir=bd["bundle_dir"])
    kwargs = runner.resolve.call_args.kwargs
    assert kwargs["neo4j_uri"] == "bolt://neo4j:7687"
    assert kwargs["neo4j_user"] == "neo4j"
    assert kwargs["neo4j_password"] == "pw"
    assert kwargs["neo4j_database"] == "neo4j"


async def test_mapforge_resolve_scopes_to_the_principals_org(tools):
    """#314: resolution runs against the caller's org (plus canonical orgs),
    derived from the principal — never from a caller argument."""
    callables, runner = tools
    with patch("micromap_mcp.auth.current_principal",
               return_value=Principal(org_id="acme", role="user")):
        bd = await callables["mapforge_create_bundle"]()
        await callables["mapforge_resolve"](bundle_dir=bd["bundle_dir"])
    assert runner.resolve.call_args.kwargs["organization_id"] == "acme"
    sig = inspect.signature(callables["mapforge_resolve"])
    assert "organization_id" not in sig.parameters


async def test_mapforge_plan_passes_principal_org_and_destination(tools):
    """Was: asserted the CALLER's org was passed through. That was the bug —
    it is now the principal's org (#299).

    Patches `micromap_mcp.auth.current_principal`, not
    `micromap_mcp.tools.mapforge.current_principal` — `mapforge_plan` derives
    its org via `auth.principal_org()` (#299 final review item 6), which
    resolves `current_principal` in auth.py's own namespace, so mapforge.py no
    longer imports the name at all."""
    callables, runner = tools
    with patch("micromap_mcp.auth.current_principal",
               return_value=Principal(org_id="acme-pharma", role="user")):
        bd = await callables["mapforge_create_bundle"]()
        await callables["mapforge_plan"](bundle_dir=bd["bundle_dir"],
                                         destination="micromap-core")
    kwargs = runner.plan.call_args.kwargs
    assert kwargs["organization_id"] == "acme-pharma"
    assert kwargs["destination"] == "micromap-core"


async def test_mapforge_emit_proxies_to_runner(tools):
    callables, runner = tools
    bd = await callables["mapforge_create_bundle"]()
    out = await callables["mapforge_emit"](bundle_dir=bd["bundle_dir"])
    runner.emit.assert_called_once()
    assert "cypher_files" in out


async def test_mapforge_approve_proxies_to_runner(tools):
    callables, runner = tools
    bd = await callables["mapforge_create_bundle"]()
    out = await callables["mapforge_approve"](
        bundle_dir=bd["bundle_dir"], reviewer="qa"
    )
    runner.approve.assert_called_once()
    assert out["reviewer"] == "qa"


async def test_mapforge_submit_passes_neo4j_creds_from_settings(tools):
    """Positive path: manifest exists + approved=True."""
    callables, runner = tools
    bd = await callables["mapforge_create_bundle"]()
    Path(bd["bundle_dir"]).joinpath("manifest.json").write_text(
        json.dumps({"approved": True, "reviewer": "qa"}),
        encoding="utf-8",
    )
    await callables["mapforge_submit"](bundle_dir=bd["bundle_dir"], reviewer="qa")
    kwargs = runner.submit.call_args.kwargs
    assert kwargs["neo4j_uri"] == "bolt://neo4j:7687"
    assert kwargs["reviewer"] == "qa"


async def test_mapforge_submit_raises_if_no_manifest(tools):
    """Pre-flight: refuse when manifest.json doesn't exist."""
    callables, _ = tools
    bd = await callables["mapforge_create_bundle"]()
    with pytest.raises(ValueError, match="no manifest.json"):
        await callables["mapforge_submit"](
            bundle_dir=bd["bundle_dir"], reviewer="qa"
        )


async def test_mapforge_submit_raises_if_not_approved(tools):
    """Pre-flight: refuse when manifest has approved=false."""
    callables, _ = tools
    bd = await callables["mapforge_create_bundle"]()
    Path(bd["bundle_dir"]).joinpath("manifest.json").write_text(
        json.dumps({"approved": False, "reviewer": "qa"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not approved"):
        await callables["mapforge_submit"](
            bundle_dir=bd["bundle_dir"], reviewer="qa"
        )


async def test_mapforge_resolve_rejects_path_traversal(tools):
    """Security: bundle_manager.resolve raises ValueError on path traversal."""
    callables, _ = tools
    with pytest.raises(ValueError):
        await callables["mapforge_resolve"](bundle_dir="../etc/passwd")


# ---------------------------------------------------------------------------
# #316: source_path and path-form schema_config are contained to the bundle
# base — mapforge_inspect / mapforge_map_heuristic were a file-read primitive
# (runner.inspect returns sample VALUES, not just metadata).
# ---------------------------------------------------------------------------


async def test_mapforge_inspect_rejects_absolute_path_outside_base(tools, tmp_path):
    callables, runner = tools
    outside = tmp_path.parent / "elsewhere" / "secrets.txt"
    with pytest.raises(ValueError):
        await callables["mapforge_inspect"](source_path=str(outside))
    runner.inspect.assert_not_called()


async def test_mapforge_inspect_rejects_traversal(tools):
    callables, runner = tools
    with pytest.raises(ValueError):
        await callables["mapforge_inspect"](source_path="../secrets.txt")
    runner.inspect.assert_not_called()


async def test_mapforge_map_heuristic_rejects_source_outside_base(tools, tmp_path):
    callables, runner = tools
    outside = tmp_path.parent / "elsewhere" / "secrets.txt"
    with pytest.raises(ValueError):
        await callables["mapforge_map_heuristic"](source_path=str(outside))
    runner.draft_heuristic.assert_not_called()


async def test_mapforge_map_heuristic_rejects_schema_config_path_outside_base(
    tools, tmp_path,
):
    callables, runner = tools
    src = tmp_path / "x.tsv"
    src.write_text("a\tb\n1\t2\n", encoding="utf-8")
    outside = tmp_path.parent / "elsewhere" / "schema_config.yaml"
    with pytest.raises(ValueError):
        await callables["mapforge_map_heuristic"](
            source_path=str(src), schema_config=str(outside),
        )
    runner.draft_heuristic.assert_not_called()


async def test_mapforge_map_heuristic_bare_template_name_passes_through(tools, tmp_path):
    """Bare built-in names ("genomics") are not paths and must keep working."""
    callables, runner = tools
    src = tmp_path / "x.tsv"
    src.write_text("a\tb\n1\t2\n", encoding="utf-8")
    out = await callables["mapforge_map_heuristic"](
        source_path=str(src), schema_config="genomics",
    )
    assert "mapping_yaml" in out
    assert runner.draft_heuristic.call_args.kwargs["schema_config"] == "genomics"


async def test_mapforge_map_heuristic_schema_config_inside_base_is_allowed(
    tools, tmp_path,
):
    callables, runner = tools
    src = tmp_path / "x.tsv"
    src.write_text("a\tb\n1\t2\n", encoding="utf-8")
    cfg = tmp_path / "custom_schema.yaml"
    cfg.write_text("name: custom\nclasses: {}\n", encoding="utf-8")
    out = await callables["mapforge_map_heuristic"](
        source_path=str(src), schema_config=str(cfg),
    )
    assert "mapping_yaml" in out
    assert runner.draft_heuristic.call_args.kwargs["schema_config"] == str(cfg.resolve())


async def test_mapforge_map_heuristic_bare_name_matching_cwd_file_is_contained(
    tools, tmp_path, tmp_path_factory, monkeypatch,
):
    """load_schema_config prefers an EXISTING FILE over a template name — so a
    bare-looking value that exists as a file relative to the server's CWD must
    be routed through containment (resolving against the bundle base), never
    passed through for load_schema_config to read out of CWD."""
    callables, runner = tools
    src = tmp_path / "x.tsv"
    src.write_text("a\tb\n1\t2\n", encoding="utf-8")
    elsewhere = tmp_path_factory.mktemp("server_cwd")
    (elsewhere / "sneaky").write_text("name: x\nclasses: {}\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)
    await callables["mapforge_map_heuristic"](
        source_path=str(src), schema_config="sneaky",
    )
    cfg_arg = runner.draft_heuristic.call_args.kwargs["schema_config"]
    assert cfg_arg == str((tmp_path / "sneaky").resolve()), (
        "schema_config resolved outside the bundle base: %r" % cfg_arg
    )


# ---------------------------------------------------------------------------
# #315: bundles have an owner — the creating principal's org — and no other
# principal can drive or read them through any pipeline step.
# ---------------------------------------------------------------------------


def _as_principal(org: str, role: str = "user", user_id: str = ""):
    return patch(
        "micromap_mcp.auth.current_principal",
        return_value=Principal(org_id=org, role=role, user_id=user_id),
    )


async def test_create_bundle_records_owner_org(tools):
    callables, _ = tools
    with _as_principal("org-a", user_id="alice"):
        bd = await callables["mapforge_create_bundle"]()
    owner = json.loads(
        (Path(bd["bundle_dir"]) / ".owner.json").read_text(encoding="utf-8")
    )
    assert owner["org"] == "org-a"
    assert owner["user"] == "alice"


async def test_second_principal_cannot_drive_anothers_bundle(tools):
    """Every pipeline step over a foreign bundle is refused before the runner
    is reached — including submit on an already-approved bundle."""
    callables, runner = tools
    with _as_principal("org-a"):
        bd = (await callables["mapforge_create_bundle"]())["bundle_dir"]
    Path(bd).joinpath("manifest.json").write_text(
        json.dumps({"approved": True, "reviewer": "qa"}), encoding="utf-8",
    )
    steps = [
        ("mapforge_resolve", {}),
        ("mapforge_plan", {}),
        ("mapforge_emit", {}),
        ("mapforge_approve", {"reviewer": "mallory"}),
        ("mapforge_submit", {"reviewer": "qa"}),
    ]
    with _as_principal("org-b"):
        for tool_name, kwargs in steps:
            with pytest.raises(ValueError):
                await callables[tool_name](bundle_dir=bd, **kwargs)
    runner.resolve.assert_not_called()
    runner.plan.assert_not_called()
    runner.emit.assert_not_called()
    runner.approve.assert_not_called()
    runner.submit.assert_not_called()


async def test_owner_can_drive_own_bundle(tools):
    callables, runner = tools
    with _as_principal("org-a"):
        bd = (await callables["mapforge_create_bundle"]())["bundle_dir"]
        await callables["mapforge_resolve"](bundle_dir=bd)
    runner.resolve.assert_called_once()


async def test_second_principal_cannot_read_anothers_staged_source(tools):
    """#316 contained reads to the bundle base; #315 closes the remaining gap
    of pointing source_path into ANOTHER caller's bundle."""
    callables, runner = tools
    with _as_principal("org-a"):
        bd = (await callables["mapforge_create_bundle"]())["bundle_dir"]
    staged = Path(bd) / "x.tsv"
    staged.write_text("a\tb\n1\t2\n", encoding="utf-8")
    with _as_principal("org-b"):
        with pytest.raises(ValueError):
            await callables["mapforge_inspect"](source_path=str(staged))
        with pytest.raises(ValueError):
            await callables["mapforge_map_heuristic"](source_path=str(staged))
    runner.inspect.assert_not_called()
    runner.draft_heuristic.assert_not_called()


async def test_legacy_unowned_bundle_belongs_to_the_default_org(tools, tmp_path):
    """Pre-#315 bundles carry no owner file: the default-org principal keeps
    using them, a mapped external org does not — consistent with how #313
    treats an unmapped caller (MCP_DEFAULT_ORG)."""
    callables, runner = tools
    legacy = tmp_path / "legacybundle00000000000000000000"
    legacy.mkdir()
    # No principal patched → current_principal() is None → default org.
    await callables["mapforge_resolve"](bundle_dir=str(legacy))
    runner.resolve.assert_called_once()
    with _as_principal("org-b"):
        with pytest.raises(ValueError):
            await callables["mapforge_emit"](bundle_dir=str(legacy))
    runner.emit.assert_not_called()


async def test_ownership_refusal_does_not_confirm_existence(tools, tmp_path):
    """A probe with a guessed id must get the same error whether the bundle
    exists (foreign-owned) or not — no existence oracle."""
    callables, _ = tools
    with _as_principal("org-a"):
        bd = (await callables["mapforge_create_bundle"]())["bundle_dir"]
    missing = str(tmp_path / "00000000000000000000000000000000")
    with _as_principal("org-b"):
        with pytest.raises(ValueError) as foreign:
            await callables["mapforge_resolve"](bundle_dir=bd)
        with pytest.raises(ValueError) as absent:
            await callables["mapforge_resolve"](bundle_dir=missing)
    foreign_msg = str(foreign.value).replace(repr(bd), "<dir>")
    absent_msg = str(absent.value).replace(repr(missing), "<dir>")
    assert foreign_msg == absent_msg


# ---------------------------------------------------------------------------
# MCP gap-fill: templates discovery tool wrappers
# ---------------------------------------------------------------------------


def test_mapforge_templates_list_returns_sorted_list_of_dicts(tools):
    """mapforge_templates_list tool delegates to runner.templates_list().

    Uses the MagicMock-runner pattern: configure the mock's return value,
    invoke the registered async tool, assert the return shape.
    """
    import asyncio
    callables, runner = tools
    runner.templates_list.return_value = [
        {"name": "genomics", "version": "1.0.0", "description": "genomics KG"},
        {"name": "microbiome", "version": "1.0.0", "description": "microbiome KG"},
        {"name": "transcriptomics", "version": "1.0.0", "description": "transcriptomics KG"},
    ]
    result = asyncio.run(callables["mapforge_templates_list"]())
    assert isinstance(result, list)
    assert len(result) == 3
    names = [e["name"] for e in result]
    assert names == sorted(names), f"not sorted: {names}"
    for entry in result:
        assert set(entry.keys()) >= {"name", "version", "description"}


def test_mapforge_templates_show_returns_yaml_content(tools):
    """mapforge_templates_show tool delegates to runner.templates_show()."""
    import asyncio
    callables, runner = tools
    runner.templates_show.return_value = {
        "name": "genomics",
        "yaml_content": "# MicroMap MapForge — genomics\nname: micromap-genomics\n",
    }
    result = asyncio.run(callables["mapforge_templates_show"]("genomics"))
    assert result["name"] == "genomics"
    assert "MicroMap MapForge — genomics" in result["yaml_content"]
    runner.templates_show.assert_called_once_with("genomics")


def test_mapforge_templates_show_unknown_name_returns_error_dict(tools):
    """mapforge_templates_show converts MapForgeError into a structured
    error dict via the existing _fmt_error pattern.

    Critical: the tool layer must NOT raise -- it returns the dict shape
    so the agent receives a parseable response, not an exception.
    """
    import asyncio
    from micromap_mcp.mapforge_runner import MapForgeError
    callables, runner = tools
    runner.templates_show.side_effect = MapForgeError(
        "templates",
        "unknown template 'not-real'. Available: genomics, microbiome, transcriptomics.",
    )
    result = asyncio.run(callables["mapforge_templates_show"]("not-real"))
    assert isinstance(result, dict)
    assert result["error"] is True
    assert result["stage"] == "templates"
    assert "not-real" in result["detail"]
    assert any(
        n in result["detail"]
        for n in ("genomics", "microbiome", "transcriptomics")
    )


def test_mapforge_map_heuristic_accepts_schema_config_kwarg(tools, tmp_path):
    """mapforge_map_heuristic tool forwards schema_config to runner.draft_heuristic.

    The tool layer pin: the new kwarg reaches the runner intact, the
    underlying MagicMock receives it as expected, and the returned dict
    shape ({mapping_yaml: <str>}) is preserved. The source lives inside the
    bundle base — outside paths are refused since #316.
    """
    import asyncio
    callables, runner = tools
    runner.draft_heuristic.return_value = (
        "source:\n  name: data\n  format: csv\n  sha256: 0\nentities:\n  - label: Gene\n"
    )

    src = tmp_path / "data.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    result = asyncio.run(callables["mapforge_map_heuristic"](
        str(src), schema_config="genomics",
    ))
    assert "mapping_yaml" in result
    assert "label: Gene" in result["mapping_yaml"]
    # Runner received the schema_config kwarg.
    runner.draft_heuristic.assert_called_once_with(
        str(src), schema_config="genomics",
    )


# ---------------------------------------------------------------------------
# #299: org comes from the authenticated principal, never the caller
# ---------------------------------------------------------------------------


async def test_mapforge_plan_no_longer_accepts_organization_id(tools):
    """The parameter IS the vulnerability — it must not exist."""
    callables, _ = tools
    sig = inspect.signature(callables["mapforge_plan"])
    assert "organization_id" not in sig.parameters


async def test_mapforge_plan_uses_the_principals_org(tools):
    callables, runner = tools
    with patch("micromap_mcp.auth.current_principal",
               return_value=Principal(org_id="acme", role="user")):
        bd = await callables["mapforge_create_bundle"]()
        await callables["mapforge_plan"](bundle_dir=bd["bundle_dir"],
                                         destination="micromap-core")
    assert runner.plan.call_args.kwargs["organization_id"] == "acme"


async def test_a_caller_cannot_cause_a_write_stamped_with_another_org(tools):
    """Negative form: the caller literally cannot ask for org-b — the
    `organization_id` kwarg was removed from the tool's signature, so passing
    it raises TypeError instead of being honoured. A normal call still stamps
    the principal's own org."""
    callables, runner = tools
    with patch("micromap_mcp.auth.current_principal",
               return_value=Principal(org_id="org-a", role="user")):
        bd = await callables["mapforge_create_bundle"]()

        with pytest.raises(TypeError):
            await callables["mapforge_plan"](bundle_dir=bd["bundle_dir"],
                                             organization_id="org-b")

        await callables["mapforge_plan"](bundle_dir=bd["bundle_dir"])
    kwargs = runner.plan.call_args.kwargs
    assert kwargs["organization_id"] == "org-a"
    assert "org-b" not in str(kwargs)
