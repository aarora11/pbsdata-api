"""Rate limiting middleware — enforces monthly request budget and per-minute burst limits."""
import time
import structlog
from datetime import datetime, timezone
from fastapi import HTTPException, Depends
from api.database import get_db
from api.middleware.auth import require_api_key

log = structlog.get_logger()

PER_MINUTE_LIMITS: dict[str, int | None] = {
    "free":       30,
    "starter":    60,
    "growth":     200,
    "scale":      600,
    "enterprise": None,  # no burst cap
}

# Module-level sliding window store: {api_key_id: (window_start_ts, count)}
_per_minute_store: dict = {}


def _check_per_minute(api_key_id, tier: str) -> tuple[int | None, int]:
    """Check per-minute burst limit. Returns (limit, remaining) for headers."""
    limit = PER_MINUTE_LIMITS.get(tier)
    if limit is None:
        return None, 0

    now = time.monotonic()
    window_start, count = _per_minute_store.get(api_key_id, (now, 0))

    if now - window_start >= 60:
        _per_minute_store[api_key_id] = (now, 1)
        return limit, limit - 1

    if count >= limit:
        retry_after = int(60 - (now - window_start))
        log.warning("rate_limit.burst_exceeded", api_key_id=str(api_key_id), tier=tier, limit=limit, retry_after=retry_after)
        raise HTTPException(
            status_code=429,
            detail={"code": "BURST_LIMIT_EXCEEDED", "message": f"Per-minute rate limit of {limit} requests exceeded."},
            headers={"Retry-After": str(retry_after)},
        )

    _per_minute_store[api_key_id] = (window_start, count + 1)
    return limit, limit - count - 1


async def check_rate_limit(
    api_key_data: dict = Depends(require_api_key),
    db=Depends(get_db),
) -> dict:
    """Check and enforce rate limits. Increments the request counter."""
    monthly_limit = api_key_data["monthly_limit"]
    requests_this_month = api_key_data["requests_this_month"]
    usage_reset_at = api_key_data["usage_reset_at"]
    api_key_id = api_key_data["id"]

    # Per-minute burst check (fast, in-memory)
    burst_limit, burst_remaining = _check_per_minute(api_key_id, api_key_data.get("tier", "free"))

    now = datetime.now(timezone.utc)

    # Normalise timezone on usage_reset_at
    if usage_reset_at is not None and usage_reset_at.tzinfo is None:
        usage_reset_at = usage_reset_at.replace(tzinfo=timezone.utc)

    # Reset counter if reset date has passed
    if usage_reset_at is not None and now >= usage_reset_at:
        from dateutil.relativedelta import relativedelta
        next_reset = usage_reset_at
        while next_reset <= now:
            next_reset = next_reset + relativedelta(months=1)
        await db.execute(
            "UPDATE api_keys SET requests_this_month = 0, usage_reset_at = $1 WHERE id = $2",
            next_reset, api_key_id,
        )
        requests_this_month = 0
        usage_reset_at = next_reset
        api_key_data["usage_reset_at"] = next_reset

    reset_ts = int(usage_reset_at.timestamp()) if usage_reset_at else 0

    # Check limit
    if requests_this_month >= monthly_limit:
        log.warning("rate_limit.monthly_exceeded", api_key_id=str(api_key_id), tier=api_key_data.get("tier"), limit=monthly_limit, used=requests_this_month)
        raise HTTPException(
            status_code=429,
            detail={"code": "RATE_LIMIT_EXCEEDED", "message": "Monthly request limit exceeded."},
            headers={
                "X-RateLimit-Limit": str(monthly_limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_ts),
                "Retry-After": str(reset_ts),
            },
        )

    # Increment counter
    await db.execute(
        "UPDATE api_keys SET requests_this_month = requests_this_month + 1 WHERE id = $1",
        api_key_id,
    )

    # Attach rate limit info for use in response headers
    api_key_data["_rl_limit"] = monthly_limit
    api_key_data["_rl_remaining"] = max(0, monthly_limit - requests_this_month - 1)
    api_key_data["_rl_reset"] = reset_ts
    api_key_data["_rl_burst_limit"] = burst_limit
    api_key_data["_rl_burst_remaining"] = burst_remaining

    return api_key_data
