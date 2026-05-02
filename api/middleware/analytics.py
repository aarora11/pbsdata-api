"""Concurrency cap for T3/T4 analytics endpoints.

Limits simultaneous heavy aggregate queries system-wide to avoid DB saturation.
Single-process safe; for multi-process deployments swap to a Redis counter.
"""
import asyncio
from fastapi import HTTPException

_analytics_semaphore = asyncio.Semaphore(10)


async def analytics_concurrency_check():
    if _analytics_semaphore._value == 0:
        raise HTTPException(
            status_code=503,
            detail={"code": "SERVICE_BUSY", "message": "Analytics capacity reached. Retry shortly."},
            headers={"Retry-After": "5"},
        )
    async with _analytics_semaphore:
        yield
