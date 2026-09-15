"""Load + validate routing-policy.yaml."""

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

_SCHEMA_PATH = Path(__file__).parent / "routing_policy.schema.json"


class RoutingPolicyValidationError(ValueError):
    """Raised when routing-policy.yaml fails schema validation."""


def _load_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_routing_policy(policy: dict[str, Any]) -> None:
    validator = Draft202012Validator(_load_schema())
    errors = sorted(validator.iter_errors(policy), key=lambda e: list(e.absolute_path))
    if errors:
        msgs = [_format_error(e) for e in errors]
        raise RoutingPolicyValidationError(
            "routing policy invalid:\n  - " + "\n  - ".join(msgs)
        )


def load_routing_policy(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    policy = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise RoutingPolicyValidationError(
            f"{p}: expected top-level object, got {type(policy).__name__}"
        )
    validate_routing_policy(policy)
    return policy


def _format_error(e: ValidationError) -> str:
    path = ".".join(str(p) for p in e.absolute_path) if e.absolute_path else "(root)"
    return f"{path}: {e.message}"
