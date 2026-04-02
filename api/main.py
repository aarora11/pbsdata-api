from fastapi import FastAPI
from contextlib import asynccontextmanager
from api.database import create_pool, close_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_pool()
    yield
    await close_pool()


app = FastAPI(title="PBSdata.io API", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}
