"""Auth utilities: HMAC-based token creation/verification and FastAPI dependencies."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

from fastapi import Header, HTTPException


def _get_secret() -> str:
    """Return AUTH_SECRET env var; generate + cache one if absent."""
    secret = os.getenv("AUTH_SECRET")
    if not secret:
        secret = secrets.token_urlsafe(32)
        os.environ["AUTH_SECRET"] = secret
    return secret


def create_access_token(email: str, ttl_seconds: int = 30 * 24 * 3600) -> str:
    """Create a signed access token encoding the given email."""
    payload = json.dumps({"email": email, "exp": time.time() + ttl_seconds})
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(
        _get_secret().encode(), encoded.encode(), hashlib.sha256
    ).hexdigest()[:24]
    return f"{encoded}.{sig}"


def verify_access_token(token: str) -> Optional[str]:
    """Verify a signed access token; return email on success, None on failure."""
    try:
        parts = token.rsplit(".", 1)
        if len(parts) != 2:
            return None
        encoded, sig = parts
        expected_sig = hmac.new(
            _get_secret().encode(), encoded.encode(), hashlib.sha256
        ).hexdigest()[:24]
        if not hmac.compare_digest(sig, expected_sig):
            return None
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if payload.get("exp", 0) < time.time():
            return None
        return payload.get("email")
    except Exception:
        return None


async def get_current_user_optional(
    authorization: Optional[str] = Header(None),
) -> Optional[str]:
    """FastAPI dependency — returns email from Bearer token, or None if not authenticated."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return verify_access_token(authorization[7:])


async def require_current_user(
    authorization: Optional[str] = Header(None),
) -> str:
    """FastAPI dependency — raises 401 if not authenticated."""
    email = await get_current_user_optional(authorization)
    if not email:
        raise HTTPException(status_code=401, detail="Authentication required")
    return email


def check_strategy_ownership(strategy, current_user: Optional[str]) -> None:
    """Raise HTTPException if user cannot modify the strategy.

    Rules:
    - Not authenticated → 401
    - strategy.user_id is None / 'default_user' → any authenticated user may modify
    - strategy.user_id is a real value and != current_user → 403
    """
    if not current_user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required to modify strategies",
        )
    owner = getattr(strategy, "user_id", None)
    if owner and owner not in ("default_user",) and owner != current_user:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to modify this strategy",
        )
