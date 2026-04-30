"""Tier-based access control — gates endpoints by subscription level."""
from fastapi import HTTPException, Depends
from api.middleware.rate_limit import check_rate_limit

# Ordinal ranking of business tiers. Higher = more access.
TIER_LEVELS: dict[str, int] = {
    "free":       0,  # Base  — raw PBS passthrough
    "starter":    1,  # T1    — Core joined endpoints
    "growth":     2,  # T2    — Clinical multi-join views
    "scale":      3,  # T3    — Intelligence full-chain
    "enterprise": 4,  # T4    — Market aggregation
}

# Human-readable spec labels used in API responses and error messages.
TIER_LABELS: dict[str, str] = {
    "free":       "Base",
    "starter":    "T1",
    "growth":     "T2",
    "scale":      "T3",
    "enterprise": "T4",
}


def require_tier(min_tier: str):
    """
    Dependency factory that enforces a minimum subscription tier.

    Usage on a route:
        @router.get("/v1/drugs/{pbs_code}")
        async def get_drug(key_data: dict = Depends(require_tier("growth"))):
            ...

    Chains through check_rate_limit so all existing auth + quota logic applies.
    Raises 403 if the key's tier is below min_tier.
    """
    min_level = TIER_LEVELS[min_tier]

    async def _check(key_data: dict = Depends(check_rate_limit)) -> dict:
        tier = key_data.get("tier", "free")
        if TIER_LEVELS.get(tier, 0) < min_level:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "TIER_INSUFFICIENT",
                    "message": (
                        f"This endpoint requires {TIER_LABELS[min_tier]} tier or above. "
                        f"Your current tier is {TIER_LABELS.get(tier, tier)}."
                    ),
                    "required_tier": TIER_LABELS[min_tier],
                    "your_tier": TIER_LABELS.get(tier, tier),
                    "upgrade_url": "https://pbsdata.io/pricing",
                },
            )
        return key_data

    return _check


def is_tier_or_above(key_data: dict, min_tier: str) -> bool:
    """
    In-handler helper for tier-aware responses on shared routes.

    Used where Base and T2+ subscribers hit the same endpoint but receive
    different response shapes (e.g. GET /v1/items/{id}).
    """
    level = TIER_LEVELS.get(key_data.get("tier", "free"), 0)
    return level >= TIER_LEVELS.get(min_tier, 0)


def tier_label(key_data: dict) -> str:
    """Return the spec-tier label (Base, T1, T2 …) for a key_data dict."""
    return TIER_LABELS.get(key_data.get("tier", "free"), "Base")
