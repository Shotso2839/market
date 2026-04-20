from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.database import engine, Base
from app.routers import markets, bets, users, ton
from app.routers import ws as ws_router
from app.routers import revenue as revenue_router
from app.routers import telegram as tg_router
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title="TON Prediction Market API",
    description="Decentralized prediction market backend on TON blockchain",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users.router, prefix="/api/v1/users", tags=["Users"])
app.include_router(markets.router, prefix="/api/v1/markets", tags=["Markets"])
app.include_router(bets.router, prefix="/api/v1/bets", tags=["Bets"])
app.include_router(ton.router, prefix="/api/v1/ton", tags=["TON Blockchain"])
app.include_router(ws_router.router, prefix="/api/v1/ws", tags=["WebSocket"])
app.include_router(tg_router.router, prefix="/api/v1/telegram", tags=["Telegram"])


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "1.0.0"}
