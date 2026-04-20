"""
WebSocket endpoints

  GET /api/v1/ws/market/{market_id}   — subscribe to a single market
  GET /api/v1/ws/user                 — personal notifications channel

Auth: Telegram initData passed as query param ?init_data=...
      (headers are not available during the WS handshake in most clients)
"""
import asyncio
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.websocket import ws_manager, emit_market_update
from app.services.ton_service import ton_service
from app.services.market_service import market_service
from app.models import User, MarketStatus
from sqlalchemy import select

log = logging.getLogger(__name__)
router = APIRouter()


async def _auth_ws(init_data: str, db: AsyncSession) -> User | None:
    """Authenticate WebSocket connection via Telegram initData query param."""
    tg_user = ton_service.verify_telegram_init_data(init_data or "")
    if not tg_user:
        return None
    result = await db.execute(
        select(User).where(User.telegram_id == tg_user.get("id"))
    )
    return result.scalar_one_or_none()


@router.websocket("/market/{market_id}")
async def ws_market(
    market_id: str,
    websocket: WebSocket,
    init_data: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
):
    """
    Subscribe to live updates for one market.

    Server pushes:
      • market_update  — pool sizes change after any bet
      • new_bet        — anonymised bet just landed
      • market_resolved — winner declared
      • ping           — keepalive every 30s

    Client can send:
      • {"action": "ping"} → server replies {"event": "pong"}
    """
    # Auth (optional for market rooms — public data)
    user = await _auth_ws(init_data, db)

    room = f"market:{market_id}"
    await ws_manager.connect(room, websocket)

    # Send current state immediately on connect
    market = await market_service.get_by_id(db, market_id)
    if market:
        await websocket.send_json({
            "event": "connected",
            "market_id": market_id,
            "status": market.status.value,
            "yes_pool_ton": round(market.yes_pool / 1_000_000_000, 4),
            "no_pool_ton": round(market.no_pool / 1_000_000_000, 4),
            "yes_pct": market.yes_pct,
            "no_pct": market.no_pct,
            "viewers": ws_manager.room_size(room),
        })
    else:
        await websocket.send_json({"event": "error", "detail": "Market not found"})
        await ws_manager.disconnect(room, websocket)
        return

    # Keepalive + receive loop
    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                msg = json.loads(raw)
                if msg.get("action") == "ping":
                    await websocket.send_json({"event": "pong"})
            except asyncio.TimeoutError:
                # Send server-side keepalive
                await websocket.send_json({
                    "event": "ping",
                    "viewers": ws_manager.room_size(room),
                })
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning(f"WS market/{market_id} error: {e}")
    finally:
        await ws_manager.disconnect(room, websocket)


@router.websocket("/user")
async def ws_user(
    websocket: WebSocket,
    init_data: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
):
    """
    Personal notifications channel (authenticated).

    Server pushes:
      • bet_confirmed   — tx verified on-chain
      • payout_ready    — user won, can claim
      • market_resolved — market the user bet on was resolved
      • ping            — keepalive every 30s
    """
    user = await _auth_ws(init_data, db)
    if not user:
        await websocket.accept()
        await websocket.send_json({"event": "error", "detail": "Unauthorized"})
        await websocket.close(code=4001)
        return

    room = f"user:{user.id}"
    await ws_manager.connect(room, websocket)

    await websocket.send_json({
        "event": "connected",
        "user_id": user.id,
        "username": user.username,
    })

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
        log.warning(f"WS user/{user.id} error: {e}")
    finally:
        await ws_manager.disconnect(room, websocket)


@router.websocket("/admin")
async def ws_admin(
    websocket: WebSocket,
    init_data: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
):
    """
    Admin dashboard feed — total connections, recent events.
    Requires an authenticated user (extend with admin role check as needed).
    """
    user = await _auth_ws(init_data, db)
    if not user:
        await websocket.accept()
        await websocket.send_json({"event": "error", "detail": "Unauthorized"})
        await websocket.close(code=4001)
        return

    room = "admin"
    await ws_manager.connect(room, websocket)
    await websocket.send_json({
        "event": "connected",
        "total_connections": ws_manager.total_connections,
    })

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "event": "stats",
                    "total_connections": ws_manager.total_connections,
                })
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(room, websocket)
