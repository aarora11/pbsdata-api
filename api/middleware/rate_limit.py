"""Rate limiting middleware — enforces monthly request budget per API key."""
from datetime import datetime, timezone
from fastapi import HTTPException, Depends
from api.database import get_db
from api.middleware.auth import require_api_key


async def check_rate_limit(
    api_key_data: dict = Depends(require_api_key),
    db=Depends(get_db),
) -> dict:
    """Check and enforce rate limits. Increments the request counter."""
    monthly_limit = api_key_data["monthly_limit"]
    requests_this_month = api_key_data["requests_this_month"]
    usage_reset_at = api_key_data["usage_reset_at"]
    api_key_id = api_key_data["id"]

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

    return api_key_data
