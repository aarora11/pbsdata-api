#!/usr/bin/env python3
"""Create an API key. Usage: uv run python scripts/create_api_key.py "Name" "email@example.com" sandbox"""
import asyncio
import sys
import asyncpg
from api.config import get_settings
from api.middleware.auth import generate_api_key

TIER_LIMITS = {
    "free":       (500,        1),
    "starter":    (10_000,     3),
    "growth":     (100_000,    12),
    "scale":      (500_000,    999),
    "enterprise": (10_000_000, 999),
}


async def create_key(name: str, email: str, tier: str):
    settings = get_settings()
    full_key, key_prefix, key_hash = generate_api_key(tier)
    monthly_limit, history_months = TIER_LIMITS.get(tier, (500, 3))
    conn = await asyncpg.connect(settings.DATABASE_URL)
    try:
        await conn.execute(
            "INSERT INTO api_keys (key_prefix, key_hash, name, customer_email, tier, monthly_limit, history_months_limit) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            key_prefix, key_hash, name, email, tier, monthly_limit, history_months,
        )
    finally:
        await conn.close()
    print(f"\nAPI Key created!\nName: {name}\nEmail: {email}\nTier: {tier}\nLimit: {monthly_limit:,}/month\n\nKey (save this — shown once):\n  {full_key}\n")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <name> <email> <tier>")
        sys.exit(1)
    asyncio.run(create_key(sys.argv[1], sys.argv[2], sys.argv[3]))
