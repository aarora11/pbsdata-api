from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql://pbsapi:pbsapi@localhost:5432/pbsapi"
    DATABASE_POOL_MIN: int = 2
    DATABASE_POOL_MAX: int = 10

    # PBS Government API
    PBS_API_BASE_URL: str = "https://api.pbs.gov.au/api/v3"
    PBS_API_SUBSCRIPTION_KEY: str = "placeholder_set_in_env"
    PBS_API_EMBARGO_KEY: str = ""
    PBS_REQUEST_DELAY_SECONDS: float = 21.0

    # Application
    APP_ENV: str = "development"
    APP_SECRET_KEY: str = "placeholder_set_in_env"
    INTERNAL_INGEST_TOKEN: str = "placeholder_set_in_env"

    # Stripe
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    # Webhooks
    WEBHOOK_SIGNING_SECRET_SALT: str = "placeholder_set_in_env"

    # Cache TTLs
    CACHE_TTL_SCHEDULE_SECONDS: int = 86400
    CACHE_TTL_META_SECONDS: int = 300

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
