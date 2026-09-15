"""#322: the MCP org-enforcement mechanism must be REACHABLE from deployment.

PR #313 shipped identity-gated enforcement, but `MCP_TOKEN_ORGS`,
`MCP_ORG_API_KEYS`, `MCP_DEFAULT_ORG` and `CANONICAL_ORGS` appeared in no
checked-in configuration — so `identity_configured()` was False in every
deployment and every MCP write landed in the canonical `default` org. Same
"complete mechanism never given a secret" shape as Workbench#670.

These are static guards: the compose file must pass the vars through, and
.env.example must document them, so setting them on the box is a one-line
.env edit rather than a compose change.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_COMPOSE = REPO_ROOT / "federation" / "docker-compose.micromap-mcp.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

ORG_VARS = ["MCP_TOKEN_ORGS", "MCP_ORG_API_KEYS", "MCP_DEFAULT_ORG", "CANONICAL_ORGS"]


def _mcp_env_list() -> list[str]:
    compose = yaml.safe_load(MCP_COMPOSE.read_text(encoding="utf-8"))
    return list(compose["services"]["micromap-mcp"]["environment"])


@pytest.mark.parametrize("var", ORG_VARS)
def test_mcp_compose_passes_org_var_through(var):
    env = _mcp_env_list()
    assert any(item.startswith(f"{var}=") for item in env), (
        f"micromap-mcp compose does not pass {var} through — the #313 org "
        f"enforcement stays inert no matter what the box .env says (#322)"
    )


@pytest.mark.parametrize("var", ORG_VARS)
def test_env_example_documents_org_var(var):
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert var in text, (
        f".env.example does not document {var} — an operator has no "
        f"discoverable path to activating MCP org enforcement (#322)"
    )
