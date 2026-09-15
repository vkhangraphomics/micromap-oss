# micromap-mapforge/micromap_mapforge/emit/routing.py
"""Rule-based routing planner.

v3 (#57): emits per-destination federation block under `destinations:` when
routing to `new-federated-instance`. Drops the legacy `federation_endpoint`
placeholder (no callers depend on it).
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def plan_route(
    mapping: dict[str, Any],
    organization_id: str,
    *,
    destination_override: str | None = None,
    policy: dict[str, Any] | None = None,
    contributor: dict[str, Any] | None = None,
    row_count: int | None = None,
) -> dict[str, Any]:
    """Pick a destination and build a routing.yaml dict.

    Precedence: destination_override > policy rules > M2 fallback ("micromap-core").
    """
    if destination_override is not None:
        destination = destination_override
    elif policy is not None:
        destination = _evaluate_policy(policy, contributor or {}, row_count)
    else:
        destination = "micromap-core"

    mapping_bytes = json.dumps(mapping, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(mapping_bytes).hexdigest()
    contributor_name = (contributor or {}).get("contributor", organization_id)

    routing: dict[str, Any] = {
        "destination": destination,
        "organization_id": organization_id,
        "provenance": {
            "contributor": contributor_name,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "mapping_version": f"sha256:{digest}",
        },
    }

    # #63: propagate provenance.enabled from policy → bundle's routing.yaml.
    # Absence on the policy ⇒ no key in the bundle (submit-side defaults to True).
    if policy is not None and "provenance" in policy:
        routing["provenance"]["enabled"] = policy["provenance"]["enabled"]

    if destination == "new-federated-instance":
        block = ((policy or {}).get("destinations", {})
                 .get("new-federated-instance", {})
                 .get("federation"))
        if not block:
            raise ValueError(
                "routing matched destination 'new-federated-instance' but policy is missing "
                "destinations.new-federated-instance.federation"
            )
        routing["destinations"] = {
            "new-federated-instance": {"federation": dict(block)}
        }

    return routing


def _evaluate_policy(
    policy: dict[str, Any],
    contributor: dict[str, Any],
    row_count: int | None,
) -> str:
    for rule in policy.get("rules", []):
        if _match(rule.get("match", {}), contributor, row_count):
            return rule["destination"]
    return policy["default"]["destination"]


def _match(
    conditions: dict[str, Any],
    contributor: dict[str, Any],
    row_count: int | None,
) -> bool:
    for key, expected in conditions.items():
        if key in ("tier", "sensitivity", "contributor"):
            if contributor.get(key) != expected:
                return False
        elif key == "min_rows":
            if row_count is None or row_count < expected:
                return False
        elif key == "max_rows":
            if row_count is None or row_count > expected:
                return False
        else:
            return False   # unknown condition key → conservative no-match
    return True
