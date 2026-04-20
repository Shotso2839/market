"""
WebSocket Connection Manager

Supports two room types:
  - market:{market_id}   — live pool updates, new bets, resolution events
  - user:{user_id}       — personal notifications (bet confirmed, payout ready)

Clients subscribe by connecting to:
  ws://localhost:8000/api/v1/ws/market/{id}?init_data=...
  ws://localhost:8000/api/v1/ws/user?init_data=...
"""
import asyncio
import json
import logging
from collections import defaultdict
from typing import DefaultDict, Set

from fastapi import WebSocket

log = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        # room_id → set of active WebSocket connections
        self._rooms: DefaultDict[str, Set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def connect(self, room: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._rooms[room].add(ws)
        log.debug(f"WS connected: room={room} total={len(self._rooms[room])}")

    async def disconnect(self, room: str, ws: WebSocket) -> None:
        async with self._lock:
            self._rooms[room].discard(ws)
            if not self._rooms[room]:
                del self._rooms[room]
        log.debug(f"WS disconnected: room={room}")

    # ── Broadcasting ───────────────────────────────────────────────────────────

    async def broadcast(self, room: str, payload: dict) -> None:
        """Send JSON payload to every socket in a room. Dead sockets are pruned."""
        message = json.dumps(payload, default=str)
        dead: list[WebSocket] = []

        for ws in list(self._rooms.get(room, [])):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            await self.disconnect(room, ws)

    async def send_personal(self, user_id: str, payload: dict) -> None:
        await self.broadcast(f"user:{user_id}", payload)

    async def broadcast_market(self, market_id: str, payload: dict) -> None:
        await self.broadcast(f"market:{market_id}", payload)

    # ── Stats ──────────────────────────────────────────────────────────────────

    def room_size(self, room: str) -> int:
        return len(self._rooms.get(room, set()))

    @property
    def total_connections(self) -> int:
        return sum(len(v) for v in self._rooms.values())


# Singleton used across the app
ws_manager = ConnectionManager()


# ── Event helpers (called from routers / services) ─────────────────────────────

async def emit_market_update(market) -> None:
    """Broadcast fresh pool data after any bet or status change."""
    await ws_manager.broadcast_market(
        market.id,
        {
            "event": "market_update",
            "market_id": market.id,
            "status": market.status.value,
            "yes_pool_ton": round(market.yes_pool / 1_000_000_000, 4),
            "no_pool_ton": round(market.no_pool / 1_000_000_000, 4),
            "total_pool_ton": round(market.total_pool / 1_000_000_000, 4),
            "yes_pct": market.yes_pct,
            "no_pct": market.no_pct,
            "winning_outcome": market.winning_outcome.value if market.winning_outcome else None,
        },
    )


async def emit_new_bet(bet, market) -> None:
    """Broadcast anonymised new-bet event to everyone watching the market."""
    await ws_manager.broadcast_market(
        market.id,
        {
            "event": "new_bet",
            "market_id": market.id,
            "outcome": bet.outcome.value,
            "amount_ton": round(bet.amount / 1_000_000_000, 4),
            "yes_pool_ton": round(market.yes_pool / 1_000_000_000, 4),
            "no_pool_ton": round(market.no_pool / 1_000_000_000, 4),
            "yes_pct": market.yes_pct,
            "no_pct": market.no_pct,
        },
    )


async def emit_bet_confirmed(bet, user_id: str) -> None:
    """Notify the bettor that their tx was confirmed on-chain."""
    await ws_manager.send_personal(
        user_id,
        {
            "event": "bet_confirmed",
            "bet_id": bet.id,
            "market_id": bet.market_id,
            "outcome": bet.outcome.value,
            "amount_ton": round(bet.amount / 1_000_000_000, 4),
            "tx_hash": bet.tx_hash,
        },
    )


async def emit_market_resolved(market) -> None:
    """Broadcast resolution to all watchers + subscribers."""
    payload = {
        "event": "market_resolved",
        "market_id": market.id,
        "winning_outcome": market.winning_outcome.value,
        "total_pool_ton": round(market.total_pool / 1_000_000_000, 4),
    }
    await ws_manager.broadcast_market(market.id, payload)


async def emit_payout_ready(bet, user_id: str) -> None:
    """Tell the winner they can claim their payout."""
    await ws_manager.send_personal(
        user_id,
        {
            "event": "payout_ready",
            "bet_id": bet.id,
            "market_id": bet.market_id,
            "payout_ton": round(bet.payout_amount / 1_000_000_000, 4) if bet.payout_amount else 0,
        },
    )
