"""#299 guard: the MCP mirror of tests/api/test_org_scoping_coverage.py.

AST-based rather than regex, because what we assert here is about function
SIGNATURES, which the REST guard never needed to inspect.

Design property carried over deliberately: exemptions are derived from the
source at test time, never a hardcoded module-name list. A name list keeps
exempting a module that later regains a query — the rot behind #269 and #298.
"""
import ast
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "micromap_mcp" / "tools"
TOOL_MODULES = sorted(p.name for p in TOOLS_DIR.glob("*.py") if p.name != "__init__.py")


def _module_ast(name: str) -> ast.Module:
    return ast.parse((TOOLS_DIR / name).read_text(encoding="utf-8"))


def _nested_function_defs(body: list):
    """Function defs reachable from `body` through statement nesting only.

    Recurses into a compound statement's own child statement lists (`If.body`/
    `.orelse`, `Try.body`/`.handlers[*].body`/`.orelse`/`.finalbody`,
    `With`/`AsyncWith`/`For`/`AsyncFor`/`While` bodies, ...) so a tool defined
    inside e.g. `if feature_flag(): async def mapforge_debug(...): ...` is
    still found. It does **not** recurse into a `FunctionDef`/`AsyncFunctionDef`
    itself once found, so a helper nested *inside* a tool's own body is never
    mistaken for a tool.

    Implemented generically via `getattr(stmt, field, None)` for
    `body`/`orelse`/`finalbody`/`handlers` rather than an `isinstance` list
    per compound-statement type, so it doesn't need updating if a statement
    kind we didn't think of (e.g. `match`) turns up carrying tools.
    """
    out = []
    for stmt in body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(stmt)
            continue
        for field in ("body", "orelse", "finalbody"):
            child_body = getattr(stmt, field, None)
            if child_body:
                out.extend(_nested_function_defs(child_body))
        for handler in getattr(stmt, "handlers", []):
            out.extend(_nested_function_defs(handler.body))
    return out


def _tool_functions(tree: ast.Module):
    """Functions (sync or async) defined inside a `register_*` function, at
    any statement-nesting depth.

    These are the MCP tools. Both the outer `register_*` match and the inner
    collection accept `FunctionDef` *and* `AsyncFunctionDef`:

    - Outer: matching only `FunctionDef` would make a `register_*` refactored
      to `async def` invisible, silently dropping the whole module from test 1.
    - Inner: matching only `AsyncFunctionDef` would make a plain sync `def`
      tool invisible. Names starting with `_` are still excluded — those are
      internal helpers (`_fmt_error`, `_caller_key`, ...), not tools.

    Collection walks through compound statements (`if`, `try`, `with`, `for`,
    `while`, ...) directly under the `register_*` body via `_nested_function_defs`
    — so a tool registered conditionally (feature flag, optional dependency) is
    still discovered — but it does not descend into a `FunctionDef`'s own body,
    so a helper nested inside a tool function is still not mistaken for a tool
    itself.
    """
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("register_")
        ):
            for fn in _nested_function_defs(node.body):
                if not fn.name.startswith("_"):
                    out.append(fn)
    return out


@pytest.mark.parametrize("name", TOOL_MODULES)
def test_no_tool_accepts_an_org_argument(name):
    """The #299 defect in its exact shape: a caller-supplied tenant.

    Known blind spot: only named parameters (`posonlyargs`/`args`/`kwonlyargs`)
    are inspected. `*args`/`**kwargs` are not, so a tool that reads
    `kwargs.get("organization_id")` would not be flagged.
    """
    offenders = []
    for fn in _tool_functions(_module_ast(name)):
        args = fn.args
        for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            # Structural, not a 3-spelling list: the point is any caller-
            # supplied tenant identifier, however it's spelled
            # (organization_id, org_id, org, tenant_id, caller_org,
            # owner_org, ...) — not just the three names #299 happened to use.
            lname = a.arg.lower()
            if "org" in lname or "tenant" in lname:
                offenders.append(f"{fn.name}({a.arg})")
    assert not offenders, (
        f"{name} exposes a caller-supplied org: {offenders}. "
        "Derive it from auth.current_principal() instead — a tool argument lets "
        "any caller write or read as another tenant (#299)."
    )


# `principal_org()` (auth.py, #299 final review item 6) is the consolidated
# `(p.org_id if p else "") or default_org()` helper — it calls
# `current_principal()` internally, so a module that only calls
# `principal_org()` still consults identity; it just does so one indirection
# away. Recognizing it here keeps this guard aligned with that consolidation
# instead of forcing every write-side module to also keep a now-redundant
# direct `current_principal()` call around purely to satisfy this test.
_IDENTITY_CONSULTING_CALLS = frozenset({"current_principal", "principal_org"})


def _calls_current_principal(tree: ast.Module) -> bool:
    """True if `tree` contains an `ast.Call` whose callee is `current_principal`
    or `principal_org` (which itself calls `current_principal`).

    Matches a bare `name(...)` (`ast.Name`) or an attribute access like
    `auth.current_principal(...)` (`ast.Attribute`). Deliberately an
    actual-call check, not a name-appears-anywhere check — see the test
    docstring below for why that distinction matters.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in _IDENTITY_CONSULTING_CALLS:
            return True
        if isinstance(func, ast.Attribute) and func.attr in _IDENTITY_CONSULTING_CALLS:
            return True
    return False


@pytest.mark.parametrize("name", TOOL_MODULES)
def test_modules_with_tools_consult_the_principal(name):
    """Any module that registers a tool must actually CALL auth.current_principal()
    — directly, or through `auth.principal_org()`, which calls it internally.

    AST-based, not a substring search of the file text: it looks for an
    `ast.Call` whose callee resolves to `current_principal` or `principal_org`.
    A substring search (`"current_principal" in src`) is satisfied by a stale
    unused import, a comment, or a docstring mentioning the name, with no
    tool ever calling the function — this check requires the token to be the
    callee of an actual call.

    Structural, not a literal list: a module with zero tool functions has
    nothing to scope and is exempt by construction, derived from the source
    at test time. This replaces a hardcoded "touches tenant data" signature
    list (`GraphDatabase` / `client.get` / `runner.`) reverse-engineered from
    today's four files — a future module reaching Neo4j through a shared
    driver helper, or REST via `client.post(...)`, would have been silently
    exempted by that list. Tying the check to "registers a tool" instead
    means there is no name to keep in sync.
    """
    tree = _module_ast(name)
    if not _tool_functions(tree):
        return
    assert _calls_current_principal(tree), (
        f"{name} registers a tool but never calls "
        "auth.current_principal() — the #299 defect verbatim (the function "
        "existed with zero callers)."
    )
