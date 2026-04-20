"""
Background tasks / cron jobs for TON Prediction Market.

Run independently alongside the API:
    python -m app.tasks

Or integrate with APScheduler / Celery Beat in production.
"""
import asyncio
import logging
from datetime import datetime, timezone

from app.database import AsyncSessionLocal
from app.services.market_service import market_service
from app.services.ton_service import ton_service
from app.models import Market, MarketStatus
from sqlalchemy import select

log = logging.getLogger(__name__)


async def close_expired_markets():
    """
    Close all markets whose bet_closes_at has passed.
    Run every 60 seconds.
    """
    async with AsyncSessionLocal() as db:
        count = await market_service.close_expired(db)
        if count:
            log.info(f"Closed {count} expired markets")


async def sync_contract_states():
    """
    For markets with a contract_address, fetch on-chain state
    and reconcile with DB (pool sizes, status).
    Run every 5 minutes.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Market).where(
                Market.contract_address.is_not(None),
                Market.status.in_([MarketStatus.OPEN, MarketStatus.CLOSED]),
            )
        )
        markets = result.scalars().all()

        for market in markets:
            try:
                state = await ton_service.get_contract_state(market.contract_address)
                if not state:
                    continue

                # Sync pools from chain (source of truth)
                market.yes_pool = state["yes_pool"]
                market.no_pool = state["no_pool"]

                status_map = {0: MarketStatus.OPEN, 1: MarketStatus.CLOSED,
                              2: MarketStatus.RESOLVED, 3: MarketStatus.CANCELLED}
                on_chain_status = status_map.get(state["status"])
                if on_chain_status and on_chain_status != market.status:
                    log.info(f"Market {market.id}: status {market.status} → {on_chain_status}")
                    market.status = on_chain_status

                db.add(market)
            except Exception as e:
                log.warning(f"Failed to sync market {market.id}: {e}")

        await db.commit()


async def run_scheduler():
    log.info("Background task scheduler started")
    tick = 0
    while True:
        try:
            await close_expired_markets()
            if tick % 5 == 0:
                await sync_contract_states()
        except Exception as e:
            log.error(f"Task error: {e}")
        tick += 1
        await asyncio.sleep(60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_scheduler())
