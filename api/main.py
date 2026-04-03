"""PBSdata.io API — FastAPI application."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from api.database import create_pool, close_pool
from api.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_pool()
    yield
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
async def trigger_ingest(request: Request, authorization: str = Header(None)):
    settings = get_settings()
    if not authorization or authorization != f"Bearer {settings.INTERNAL_INGEST_TOKEN}":
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Invalid ingest token."})
    body = await request.json()
    month = body.get("month")
    if not month:
        raise HTTPException(status_code=422, detail="month is required")
    return {"status": "accepted", "month": month}


from api.routers import medicines, items, changes, schedules, webhooks
app.include_router(medicines.router, prefix="/v1")
app.include_router(items.router, prefix="/v1")
app.include_router(changes.router, prefix="/v1")
app.include_router(schedules.router, prefix="/v1")
app.include_router(webhooks.router, prefix="/v1")
