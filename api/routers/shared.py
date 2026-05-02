from fastapi import Response


def _rl(response: Response, d: dict) -> None:
    response.headers["X-RateLimit-Limit"] = str(d.get("_rl_limit", 0))
    response.headers["X-RateLimit-Remaining"] = str(d.get("_rl_remaining", 0))
    response.headers["X-RateLimit-Reset"] = str(d.get("_rl_reset", 0))
    burst_limit = d.get("_rl_burst_limit")
    response.headers["X-RateLimit-Burst-Limit"] = "unlimited" if burst_limit is None else str(burst_limit)
    burst_remaining = d.get("_rl_burst_remaining", 0)
    response.headers["X-RateLimit-Burst-Remaining"] = "unlimited" if burst_limit is None else str(burst_remaining)
