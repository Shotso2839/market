"""
WebSocket endpoints.

GET /api/v1/ws/market/{market_id} -> subscribe to a single market
GET /api/v1/ws/user -> personal notifications channel

Auth: Telegram initData passed as query param ?init_data=...
because headers are not reliably available during the WebSocket handshake.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import User
from app.services.market_service import market_service
from app.services.ton_service import ton_service
from app.websocket import ws_manager

log = logging.getLogger(__name__)
router = APIRouter()


async def _auth_ws(init_data: str, db: AsyncSession) -> User | None:
    """Authenticate a WebSocket connection via Telegram initData."""
    tg_user = ton_service.verify_telegram_init_data(init_data or "")
    if not tg_user:
        return None

    result = await db.execute(
        select(User).where(User.telegram_id == tg_user.get("id"))
    )
    return result.scalar_one_or_none()


async def _get_ws_user(init_data: str) -> User | None:
    async with AsyncSessionLocal() as db:
        return await _auth_ws(init_data, db)


async def _get_market_snapshot(market_id: str):
    async with AsyncSessionLocal() as db:
        return await market_service.get_by_id(db, market_id)


@router.websocket("/market/{market_id}")
async def ws_market(
    market_id: str,
    websocket: WebSocket,
    init_data: str = Query(default=""),
):
    """
    Subscribe to live updates for one market.

    Server pushes:
      - market_update
      - new_bet
      - market_resolved
      - ping

    Client can send:
      - {"action": "ping"} -> {"event": "pong"}
    """
    room = f"market:{market_id}"
    await ws_manager.connect(room, websocket)

    market = await _get_market_snapshot(market_id)
    if not market:
        await websocket.send_json({"event": "error", "detail": "Market not found"})
        await ws_manager.disconnect(room, websocket)
        await websocket.close(code=4404)
        return

    await websocket.send_json(
        {
            "event": "connected",
            "market_id": market_id,
            "status": market.status.value,
            "yes_pool_ton": round(market.yes_pool / 1_000_000_000, 4),
            "no_pool_ton": round(market.no_pool / 1_000_000_000, 4),
            "yes_pct": market.yes_pct,
            "no_pct": market.no_pct,
            "viewers": ws_manager.room_size(room),
        }
    )

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                msg = json.loads(raw)
                if msg.get("action") == "ping":
                    await websocket.send_json({"event": "pong"})
            except asyncio.TimeoutError:
                await websocket.send_json(
                    {
                        "event": "ping",
                        "viewers": ws_manager.room_size(room),
                    }
                )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning("WS market/%s error: %s", market_id, e)
    finally:
        await ws_manager.disconnect(room, websocket)


@router.websocket("/user")
async def ws_user(
    websocket: WebSocket,
    init_data: str = Query(default=""),
):
    """
    Personal notifications channel.

    Server pushes:
      - bet_confirmed
      - payout_ready
      - market_resolved
      - ping
    """
    user = await _get_ws_user(init_data)
    if not user:
        await websocket.accept()
        await websocket.send_json({"event": "error", "detail": "Unauthorized"})
        await websocket.close(code=4001)
        return

    room = f"user:{user.id}"
    await ws_manager.connect(room, websocket)
    await websocket.send_json(
        {
            "event": "connected",
            "user_id": user.id,
            "username": user.username,
        }
    )

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                msg = json.loads(raw)
                if msg.get("action") == "ping":
                    await websocket.send_json({"event": "pong"})
            except asyncio.TimeoutError:
                await websocket.send_json({"event": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning("WS user/%s error: %s", user.id, e)
    finally:
        await ws_manager.disconnect(room, websocket)


@router.websocket("/admin")
async def ws_admin(
    websocket: WebSocket,
    init_data: str = Query(default=""),
):
    """Admin dashboard feed."""
    user = await _get_ws_user(init_data)
    if not user:
        await websocket.accept()
        await websocket.send_json({"event": "error", "detail": "Unauthorized"})
        await websocket.close(code=4001)
        return

    room = "admin"
    await ws_manager.connect(room, websocket)
    await websocket.send_json(
        {
            "event": "connected",
            "total_connections": ws_manager.total_connections,
        }
    )

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                await websocket.send_json(
                    {
                        "event": "stats",
                        "total_connections": ws_manager.total_connections,
                    }
                )
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(room, websocket)
