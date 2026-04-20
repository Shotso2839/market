"""
app/oracles/dispatcher.py
Диспетчер оракулов.

Для каждого рынка с oracle_type='pyth'|'sports'|'weather':
  1. Выбирает нужный оракул по oracle_params.type
  2. Запрашивает данные
  3. Если confidence >= MIN_CONFIDENCE → вызывает market_service.resolve()
  4. Записывает результат в oracle_log

Вызывается из tasks.py каждые N минут для всех закрытых рынков.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Market, MarketStatus, OracleType
from app.oracles.base import BaseOracle, OracleResult
from app.oracles.crypto  import CryptoOracle
from app.oracles.sports  import SportsOracle
from app.oracles.weather import WeatherOracle
from app.schemas import MarketResolve
from app.models import BetOutcome

log = logging.getLogger(__name__)

# Минимальная уверенность оракула для авто-разрешения
MIN_CONFIDENCE = 0.8

# Маппинг oracle_params.type → класс оракула
_ORACLE_MAP: dict[str, BaseOracle] = {
    "crypto":  CryptoOracle(),
    "pyth":    CryptoOracle(),       # алиас
    "sports":  SportsOracle(),
    "football": SportsOracle(),      # алиас
    "weather": WeatherOracle(),
}


async def run_oracle_for_market(
    market: Market,
    db: AsyncSession,
) -> Optional[OracleResult]:
    """
    Запускает оракул для одного рынка.
    Если оракул возвращает outcome и confidence достаточная → разрешает рынок.
    Возвращает OracleResult или None при ошибке.
    """
    params = market.oracle_params or {}
    oracle_type = params.get("type", "").lower()

    oracle = _ORACLE_MAP.get(oracle_type)
    if oracle is None:
        log.warning(f"Market {market.id}: no oracle for type='{oracle_type}'")
        return None

    log.info(f"Oracle [{oracle.name}] checking market: {market.title[:60]}")

    try:
        result = await oracle.resolve(market)
    except Exception as e:
        log.exception(f"Oracle error for market {market.id}: {e}")
        return None

    if result.error:
        log.warning(f"Market {market.id}: oracle error — {result.error}")
        return result

    if result.outcome is None:
        log.info(f"Market {market.id}: oracle says 'not ready yet' — {result.source_value}")
        return result

    if result.confidence < MIN_CONFIDENCE:
        log.info(
            f"Market {market.id}: oracle confidence too low "
            f"({result.confidence:.2f} < {MIN_CONFIDENCE}) — skipping"
        )
        return result

    # Разрешаем рынок
    log.info(
        f"Market {market.id}: auto-resolving → outcome={result.outcome} "
        f"confidence={result.confidence:.2f} source='{result.source_value}'"
    )

    await _auto_resolve(market, result, db)
    return result


async def _auto_resolve(
    market: Market,
    result: OracleResult,
    db: AsyncSession,
) -> None:
    """Вызывает market_service.resolve() и рассылает уведомления."""
    from app.services.market_service import market_service
    from app.schemas import MarketResolve
    from app.models import BetOutcome

    outcome_enum = BetOutcome.YES if result.outcome == "yes" else BetOutcome.NO

    data = MarketResolve(
        winning_outcome=outcome_enum,
        resolution_tx_hash=None,
    )

    try:
        market = await market_service.resolve(db, market, data)
        await db.commit()

        # WebSocket уведомление всем в комнате рынка
        try:
            from app.websocket import emit_market_resolved
            await emit_market_resolved(market)
        except Exception:
            pass

        # Telegram уведомления участникам
        try:
            from sqlalchemy import select as sa_select
            from app.models import Bet, User
            from app.routers.telegram import notify_market_resolved

            bets_result = await db.execute(
                sa_select(Bet).where(Bet.market_id == market.id)
            )
            bets = bets_result.scalars().all()

            for bet in bets:
                user_res = await db.execute(
                    sa_select(User).where(User.id == bet.user_id)
                )
                user = user_res.scalar_one_or_none()
                if user and user.telegram_id:
                    import asyncio
                    payout_ton = round(bet.payout_amount / 1_000_000_000, 4) if bet.payout_amount else 0.0
                    asyncio.create_task(notify_market_resolved(
                        user.telegram_id,
                        market.title,
                        result.outcome,
                        bet.is_winner or False,
                        payout_ton,
                    ))
        except Exception as e:
            log.warning(f"Failed to send notifications: {e}")

    except Exception as e:
        log.exception(f"Auto-resolve failed for market {market.id}: {e}")
        await db.rollback()


async def run_all_oracle_markets(db: AsyncSession) -> int:
    """
    Обрабатывает все рынки которые:
    - Статус: closed (bet_closes_at прошло)
    - oracle_type: pyth, sports, weather (НЕ self)
    - Ещё не разрешены

    Возвращает количество успешно разрешённых рынков.
    """
    result = await db.execute(
        select(Market).where(
            Market.status == MarketStatus.CLOSED,
            Market.oracle_type != OracleType.CREATOR,
            Market.winning_outcome.is_(None),
        )
    )
    markets = result.scalars().all()

    if not markets:
        return 0

    log.info(f"Running oracles for {len(markets)} pending markets")
    resolved = 0

    for market in markets:
        oracle_result = await run_oracle_for_market(market, db)
        if oracle_result and oracle_result.outcome is not None:
            resolved += 1

    return resolved
