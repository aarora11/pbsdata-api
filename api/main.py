"""PBSdata.io API — FastAPI application."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import httpx
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request, HTTPException, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from api.database import create_pool, close_pool, get_pool
from api.config import get_settings

log = structlog.get_logger()
_scheduler = AsyncIOScheduler(timezone="UTC")


async def _alert_slack(message: str) -> None:
    settings = get_settings()
    if not settings.ALERT_SLACK_WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(settings.ALERT_SLACK_WEBHOOK_URL, json={"text": message})
    except Exception as exc:
        log.warning("alert.slack_failed", error=str(exc))


async def _run_ingest_and_notify(month: str, schedule_date: str, is_embargo: bool):
    from ingest.runner import run_ingest
    from api.services.webhook_sender import deliver_webhook

    pool = await get_pool()

    try:
        await run_ingest(pool, month, schedule_date, is_embargo=is_embargo)
    except Exception as exc:
        log.error("ingest.background_failed", month=month, error=str(exc))
        await _alert_slack(f":x: *PBSdata ingest failed* for `{month}`\n```{exc}```")
        return

    # Fire pbs.schedule.released webhooks
    try:
        async with pool.acquire() as conn:
            webhooks = await conn.fetch(
                """
                SELECT id, endpoint_url, signing_secret
                FROM webhooks
                WHERE is_active = true
                  AND 'pbs.schedule.released' = ANY(event_types)
                """
            )
        payload = {"month": month, "is_embargo": is_embargo}
        for wh in webhooks:
            async with pool.acquire() as conn:
                await deliver_webhook(dict(wh), "pbs.schedule.released", payload, db=conn)
    except Exception as exc:
        log.error("ingest.webhook_notify_failed", month=month, error=str(exc))


async def _scheduled_monthly_ingest():
    """Called by APScheduler on the 1st of each month at 06:00 UTC."""
    now = datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")
    schedule_date = f"{month}-01"

    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT ingest_status FROM schedules WHERE month = $1", month
        )
    if status == "complete":
        log.info("cron.ingest_already_complete", month=month)
        return

    log.info("cron.ingest_starting", month=month)
    await _run_ingest_and_notify(month, schedule_date, is_embargo=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_pool()
    _scheduler.add_job(
        _scheduled_monthly_ingest,
        "cron",
        day=1,
        hour=6,
        minute=0,
        id="monthly_ingest",
        replace_existing=True,
    )
    _scheduler.start()
    yield
    _scheduler.shutdown(wait=False)
    await close_pool()


app = FastAPI(title="PBSdata.io API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
    expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.post("/internal/ingest")
async def trigger_ingest(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str = Header(None),
):
    settings = get_settings()
    if not authorization or authorization != f"Bearer {settings.INTERNAL_INGEST_TOKEN}":
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Invalid ingest token."})
    body = await request.json()
    month = body.get("month")
    if not month:
        raise HTTPException(status_code=422, detail="month is required")
    schedule_date = body.get("schedule_date", f"{month}-01")
    is_embargo = body.get("is_embargo", False)
    background_tasks.add_task(_run_ingest_and_notify, month, schedule_date, is_embargo)
    return {"status": "accepted", "month": month}


from api.routers import (
    medicines, items, changes, schedules, webhooks,
    fees, prescribing_texts, indications, amt, dispensing_rules, summary_of_changes,
    organisations, programs, atc_codes, copayments, restrictions,
)
from api.routers.auth import router as auth_router

app.include_router(auth_router, prefix="/v1")
app.include_router(medicines.router, prefix="/v1")
app.include_router(items.router, prefix="/v1")
app.include_router(restrictions.router, prefix="/v1")
app.include_router(changes.router, prefix="/v1")
app.include_router(schedules.router, prefix="/v1")
app.include_router(webhooks.router, prefix="/v1")
app.include_router(fees.router, prefix="/v1")
app.include_router(prescribing_texts.router, prefix="/v1")
app.include_router(indications.router, prefix="/v1")
app.include_router(amt.router, prefix="/v1")
app.include_router(dispensing_rules.router, prefix="/v1")
app.include_router(summary_of_changes.router, prefix="/v1")
app.include_router(organisations.router, prefix="/v1")
app.include_router(programs.router, prefix="/v1")
app.include_router(atc_codes.router, prefix="/v1")
app.include_router(copayments.router, prefix="/v1")
