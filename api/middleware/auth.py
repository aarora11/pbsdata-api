import hashlib
import secrets
import structlog
from fastapi import Header, HTTPException, Depends
from api.database import get_db

log = structlog.get_logger()


def generate_api_key(tier: str = "free") -> tuple[str, str, str]:
    """Returns (full_key, key_prefix, key_hash). full_key shown once, never stored."""
    prefix = "sk_test_" if tier == "free" else "sk_live_"
    raw = secrets.token_urlsafe(32)
    full_key = f"{prefix}{raw}"
    key_prefix = full_key[:12]
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, key_prefix, key_hash


def hash_api_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode()).hexdigest()


async def require_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    db=Depends(get_db),
) -> dict:
    """FastAPI dependency. Validates the API key. Raises 401 if invalid."""
    if not x_api_key or len(x_api_key) < 20:
        log.warning("auth.rejected", reason="missing_or_malformed", key_prefix=None)
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_API_KEY", "message": "Missing or malformed API key."}
        )
    key_hash = hash_api_key(x_api_key)
    row = await db.fetchrow(
        """
        SELECT id, tier, monthly_limit, requests_this_month,
               history_months_limit, is_active, usage_reset_at, customer_email
        FROM api_keys WHERE key_hash = $1
        """,
        key_hash,
    )
    if not row or not row["is_active"]:
        log.warning("auth.rejected", reason="invalid_or_revoked", key_prefix=x_api_key[:12])
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_API_KEY", "message": "Invalid or revoked API key."}
        )
    # Update last_used_at inline (fire-and-forget not safe with single connection)
    try:
        await db.execute("UPDATE api_keys SET last_used_at = NOW() WHERE key_hash = $1", key_hash)
    except Exception:
        pass
    return dict(row)
