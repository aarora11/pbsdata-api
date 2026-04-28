"""Self-serve API key provisioning."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from api.database import get_db
from api.middleware.auth import generate_api_key, require_api_key

router = APIRouter(prefix="/auth", tags=["auth"])

TIER_LIMITS: dict[str, tuple[int, int]] = {
    "free":       (500,        1),
    "starter":    (10_000,     3),
    "growth":     (100_000,    12),
    "scale":      (500_000,    999),
    "enterprise": (10_000_000, 999),
}


class KeyCreateRequest(BaseModel):
    name: str
    email: EmailStr


class KeyCreateResponse(BaseModel):
    key: str
    key_prefix: str
    tier: str
    monthly_limit: int
    history_months_limit: int
    message: str


@router.post("/keys", response_model=KeyCreateResponse, status_code=201)
async def create_api_key(body: KeyCreateRequest, db=Depends(get_db)):
    """Create a free-tier API key. Key is returned once — store it securely."""
    existing = await db.fetchval(
        "SELECT COUNT(*) FROM api_keys WHERE customer_email = $1 AND tier = 'free' AND is_active = true",
        body.email,
    )
    if existing >= 3:
        raise HTTPException(
            status_code=429,
            detail={"code": "KEY_LIMIT_REACHED", "message": "Maximum of 3 active free keys per email."},
        )

    monthly_limit, history_months = TIER_LIMITS["free"]
    full_key, key_prefix, key_hash = generate_api_key("free")

    await db.execute(
        """
        INSERT INTO api_keys (key_prefix, key_hash, name, customer_email, tier, monthly_limit, history_months_limit)
        VALUES ($1, $2, $3, $4, 'free', $5, $6)
        """,
        key_prefix, key_hash, body.name, body.email, monthly_limit, history_months,
    )

    return KeyCreateResponse(
        key=full_key,
        key_prefix=key_prefix,
        tier="free",
        monthly_limit=monthly_limit,
        history_months_limit=history_months,
        message="Store this key securely — it will not be shown again.",
    )


@router.get("/keys/me")
async def get_current_key(key_data: dict = Depends(require_api_key)):
    """Return metadata for the authenticated API key."""
    return {
        "key_prefix": None,
        "tier": key_data["tier"],
        "monthly_limit": key_data["monthly_limit"],
        "requests_this_month": key_data["requests_this_month"],
        "history_months_limit": key_data["history_months_limit"],
        "usage_reset_at": key_data["usage_reset_at"],
        "is_active": key_data["is_active"],
    }


@router.delete("/keys/me", status_code=204)
async def revoke_current_key(key_data: dict = Depends(require_api_key), db=Depends(get_db)):
    """Revoke the authenticated API key."""
    await db.execute("UPDATE api_keys SET is_active = false WHERE id = $1", key_data["id"])
