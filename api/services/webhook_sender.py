"""Webhook delivery service with retry logic."""
import hmac
import hashlib
import json
import asyncio
from datetime import datetime, timezone
from typing import Any
import httpx
import structlog

logger = structlog.get_logger()

VALID_EVENTS = {
    "pbs.schedule.released",
    "medicine.new",
    "medicine.price_changed",
    "medicine.delisted",
    "medicine.sixty_day_added",
    "medicine.restriction_changed",
}

RETRY_DELAYS = [0, 300, 1800]  # 0s, 5min, 30min


def compute_signature(secret: str, body: bytes) -> str:
    """Compute HMAC-SHA256 signature for webhook payload."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _attempt_delivery(
    endpoint_url: str,
    body: bytes,
    headers: dict,
) -> tuple[bool, int, str]:
    """
    Make a single delivery attempt.
    Returns (success, status_code, response_body).
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(endpoint_url, content=body, headers=headers)
            success = 200 <= response.status_code < 300
            return success, response.status_code, response.text[:1000]
    except Exception as exc:
        return False, 0, str(exc)[:1000]


async def deliver_webhook(
    webhook: dict,
    event_type: str,
    payload: Any,
    db=None,
    retry_delays: list[int] = None,
) -> bool:
    """
    Deliver a webhook with up to 3 attempts.
    Logs all attempts to webhook_delivery_log.
    Increments failure_count on the webhook on final failure.
    Returns True on success, False on final failure.
    """
    if retry_delays is None:
        retry_delays = RETRY_DELAYS

    body_dict = {
        "event": event_type,
        "data": payload,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
    }
    body = json.dumps(body_dict, default=str).encode()

    headers = {
        "Content-Type": "application/json",
        "X-PBSData-Event": event_type,
    }

    signing_secret = webhook.get("signing_secret")
    if signing_secret:
        sig = compute_signature(signing_secret, body)
        headers["X-PBSData-Signature"] = f"sha256={sig}"

    webhook_id = webhook["id"]
    endpoint_url = webhook["endpoint_url"]

    for attempt_num, delay in enumerate(retry_delays, start=1):
        if delay > 0:
            await asyncio.sleep(delay)

        success, status_code, response_body = await _attempt_delivery(endpoint_url, body, headers)

        if db is not None:
            try:
                await db.execute(
                    """
                    INSERT INTO webhook_delivery_log
                        (webhook_id, event_type, payload, response_status, response_body, success, attempt_number)
                    VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7)
                    """,
                    webhook_id,
                    event_type,
                    json.dumps(body_dict, default=str),
                    status_code if status_code else None,
                    response_body,
                    success,
                    attempt_num,
                )
            except Exception as log_err:
                logger.warning("webhook.log_failed", error=str(log_err))

        if success:
            if db is not None:
                try:
                    await db.execute(
                        "UPDATE webhooks SET last_triggered_at = NOW(), failure_count = 0 WHERE id = $1",
                        webhook_id,
                    )
                except Exception:
                    pass
            logger.info("webhook.delivered", webhook_id=str(webhook_id), attempt=attempt_num)
            return True

    # All attempts failed
    if db is not None:
        try:
            await db.execute(
                "UPDATE webhooks SET failure_count = failure_count + 1 WHERE id = $1",
                webhook_id,
            )
        except Exception:
            pass

    logger.warning("webhook.failed", webhook_id=str(webhook_id))
    return False
