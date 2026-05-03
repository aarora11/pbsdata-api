"""Webhooks router — full implementation."""
import secrets
import hashlib
from fastapi import APIRouter, Depends, Response, HTTPException
from pydantic import BaseModel
from api.middleware.rate_limit import check_rate_limit
from api.database import get_db
from api.services.webhook_sender import VALID_EVENTS

router = APIRouter(tags=["webhooks"])

WEBHOOK_TIERS = {"starter", "growth", "scale", "enterprise"}


class WebhookCreate(BaseModel):
    endpoint_url: str
    event_types: list[str]


@router.post(
    "/webhooks",
    status_code=201,
    summary="Create Webhook",
    description=(
        "Registers a new webhook endpoint to receive real-time notifications when PBS schedule events occur. "
        "The URL must use HTTPS. On creation, a one-time `signing_secret` is returned — store it securely "
        "to verify incoming webhook payloads. Supported event types are defined in the webhook sender service.\n\n"
        "Requires **Starter (T1)** tier or above."
    ),
)
async def create_webhook(
    body: WebhookCreate,
    response: Response,
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    # Tier check
    if api_key_data.get("tier") not in WEBHOOK_TIERS:
        raise HTTPException(
            status_code=403,
            detail={"code": "TIER_REQUIRED", "message": "Webhooks require Core tier or above."},
        )

    # URL validation — must be HTTPS
    if not body.endpoint_url.startswith("https://"):
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_URL", "message": "Webhook URL must use HTTPS."},
        )

    # Validate event names
    invalid_events = [e for e in body.event_types if e not in VALID_EVENTS]
    if invalid_events:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_EVENT", "message": f"Unknown event types: {invalid_events}"},
        )

    if not body.event_types:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_EVENT", "message": "At least one event type is required."},
        )

    # Generate signing secret
    signing_secret = secrets.token_urlsafe(32)
    secret_hash = hashlib.sha256(signing_secret.encode()).hexdigest()

    api_key_id = api_key_data["id"]
    webhook_id = await db.fetchval(
        """
        INSERT INTO webhooks (api_key_id, endpoint_url, event_types, signing_secret, secret_hash)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
        """,
        api_key_id,
        body.endpoint_url,
        body.event_types,
        signing_secret,
        secret_hash,
    )

    return {
        "id": str(webhook_id),
        "endpoint_url": body.endpoint_url,
        "event_types": body.event_types,
        "signing_secret": signing_secret,  # shown once
        "is_active": True,
        "failure_count": 0,
    }


@router.get(
    "/webhooks",
    summary="List Active Webhooks",
    description=(
        "Returns all active webhooks registered for the authenticated API key, "
        "including endpoint URL, subscribed event types, failure count, and last triggered timestamp.\n\n"
        "Requires **Starter (T1)** tier or above."
    ),
)
async def list_webhooks(
    response: Response,
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    api_key_id = api_key_data["id"]
    rows = await db.fetch(
        """
        SELECT id, endpoint_url, event_types, is_active, failure_count, last_triggered_at, created_at
        FROM webhooks
        WHERE api_key_id = $1 AND is_active = TRUE
        ORDER BY created_at DESC
        """,
        api_key_id,
    )
    data = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        data.append(d)
    return {"data": data}


@router.delete(
    "/webhooks/{webhook_id}",
    status_code=204,
    summary="Delete Webhook",
    description=(
        "Deactivates and removes a registered webhook by its UUID. "
        "Returns 204 No Content on success. Returns 404 if the webhook does not exist "
        "or does not belong to the authenticated API key.\n\n"
        "Requires **Starter (T1)** tier or above."
    ),
)
async def delete_webhook(
    webhook_id: str,
    api_key_data: dict = Depends(check_rate_limit),
    db=Depends(get_db),
):
    import uuid as uuid_mod
    try:
        wid = uuid_mod.UUID(webhook_id)
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Webhook not found."})

    api_key_id = api_key_data["id"]
    result = await db.execute(
        "UPDATE webhooks SET is_active = FALSE WHERE id = $1 AND api_key_id = $2",
        wid, api_key_id,
    )
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Webhook not found."})
