"""
Rate Limiting for MicroMap API

Uses slowapi to limit requests per API key.
"""

import os
from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request


def get_api_key_or_ip(request: Request) -> str:
    """
    Get rate limit key - uses API key if present, otherwise IP address.

    This ensures rate limits are applied per-client appropriately.
    """
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"key:{api_key}"
    return f"ip:{get_remote_address(request)}"


# Get rate limit from environment (default: 100 requests per minute)
RATE_LIMIT = os.environ.get("RATE_LIMIT_PER_MINUTE", "100")
DEFAULT_RATE_LIMIT = f"{RATE_LIMIT}/minute"

# Create the limiter instance
limiter = Limiter(key_func=get_api_key_or_ip)
