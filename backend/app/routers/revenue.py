"""
app/routers/revenue.py
Эндпоинты монетизации и аналитики доходов.

  GET  /api/v1/revenue/platform     — суммарная выручка платформы (admin)
  GET  /api/v1/revenue/my-earnings  — заработок создателя рынков
  GET  /api/v1/revenue/market/{id}  — доход конкретного рынка
  GET  /api/v1/revenue/fee-preview  — предпросмотр комиссии перед ставкой
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, Market, Bet, MarketStatus
from app.fees import FEE, calculate_bet_fee, calculate_resolution_fee, NANO
from app.rate_limit import limiter, LIMIT_READ

router = APIRouter()


# ── Fee preview (публичный) ──────────────────────────────────────────────────

@router.get("/fee-preview")
@limiter.limit(LIMIT_READ)
async def fee_preview(
    request: Request,
    amount_ton: float = Query(..., gt=0, description="Сумма ставки в TON"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Предпросмотр комиссии перед подтверждением ставки.
    Фронтенд показывает это пользователю в модале ставки.
    """
    amount_nano = int(amount_ton * NANO)

    # Считаем сколько ставок пользователь уже сделал
    result = await db.execute(
        select(func.count(Bet.id)).where(
            Bet.user_id == current_user.id,
        )
    )
    bet_count = result.scalar_one() or 0

    fee = calculate_bet_fee(amount_nano, bet_count)

    return {
        "amount_ton":      amount_ton,
        "fee_ton":         fee.fee_ton,
        "net_ton":         fee.net_ton,
        "fee_pct":         round(FEE.taker_bps / 100, 2),
        "is_free_bet":     fee.is_free,
        "free_bets_left":  max(0, FEE.free_bets_count - bet_count),
        "fee_breakdown": {
            "platform_ton": round(fee.platform_share / NANO, 6),
            "creator_ton":  round(fee.creator_share  / NANO, 6),
        },
    }


# ── Creator earnings ─────────────────────────────────────────────────────────

@router.get("/my-earnings")
@limiter.limit(LIMIT_READ)
async def my_earnings(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Доход создателя рынков: 20% от всех комиссий по его рынкам.
    """
    result = await db.execute(
        select(
            func.count(Market.id).label("markets_count"),
            func.sum(Market.taker_fee_collected).label("total_taker"),
            func.sum(Market.resolution_fee_collected).label("total_resolution"),
            func.sum(Market.creator_fee_earned).label("total_creator"),
        ).where(Market.creator_id == current_user.id)
    )
    row = result.one()

    total_taker      = row.total_taker      or 0
    total_resolution = row.total_resolution or 0
    total_creator    = row.total_creator    or 0

    # Рынки с наибольшим доходом создателя
    top_markets_result = await db.execute(
        select(Market.id, Market.title, Market.creator_fee_earned)
        .where(
            Market.creator_id == current_user.id,
            Market.creator_fee_earned > 0,
        )
        .order_by(Market.creator_fee_earned.desc())
        .limit(5)
    )
    top = top_markets_result.all()

    return {
        "total_markets_created": row.markets_count or 0,
        "total_earned_ton":      round(total_creator    / NANO, 4),
        "from_taker_fees_ton":   round(total_taker * FEE.creator_share_bps / 10000 / NANO, 4),
        "from_resolution_fees_ton": round(total_resolution * FEE.creator_share_bps / 10000 / NANO, 4),
        "top_markets": [
            {
                "id":          r.id,
                "title":       r.title[:60],
                "earned_ton":  round((r.creator_fee_earned or 0) / NANO, 4),
            }
            for r in top
        ],
        "fee_structure": {
            "creator_share_pct":    FEE.creator_share_bps / 100,
            "taker_fee_pct":        FEE.taker_bps / 100,
            "resolution_fee_pct":   FEE.resolution_bps / 100,
            "free_bets_per_user":   FEE.free_bets_count,
        },
    }


# ── Single market revenue ────────────────────────────────────────────────────

@router.get("/market/{market_id}")
@limiter.limit(LIMIT_READ)
async def market_revenue(
    request: Request,
    market_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Доход по конкретному рынку (только для создателя или платформы)."""
    market = (await db.execute(
        select(Market).where(Market.id == market_id)
    )).scalar_one_or_none()

    if not market:
        raise HTTPException(status_code=404, detail="Market not found")

    if market.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only market creator can view this")

    bets_result = await db.execute(
        select(
            func.count(Bet.id).label("bet_count"),
            func.sum(Bet.fee_nano).label("total_fee"),
            func.sum(Bet.amount).label("total_volume"),
        ).where(Bet.market_id == market_id)
    )
    bets_row = bets_result.one()

    total_fee    = (market.taker_fee_collected or 0) + (market.resolution_fee_collected or 0)
    creator_cut  = market.creator_fee_earned or 0
    platform_cut = total_fee - creator_cut

    return {
        "market_id":    market_id,
        "title":        market.title,
        "status":       market.status.value,
        "total_pool_ton":  round(market.total_pool / NANO, 4),
        "bet_count":       bets_row.bet_count or 0,
        "total_volume_ton": round((bets_row.total_volume or 0) / NANO, 4),
        "fees": {
            "taker_collected_ton":      round((market.taker_fee_collected or 0) / NANO, 4),
            "resolution_collected_ton": round((market.resolution_fee_collected or 0) / NANO, 4),
            "total_fees_ton":           round(total_fee / NANO, 4),
            "your_earnings_ton":        round(creator_cut / NANO, 4),
            "platform_earnings_ton":    round(platform_cut / NANO, 4),
        },
    }


# ── Platform-wide revenue (admin) ────────────────────────────────────────────

@router.get("/platform")
@limiter.limit("10/minute")
async def platform_revenue(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Суммарная выручка платформы за последние N дней.
    В production защитить проверкой admin-роли.
    """
    from datetime import datetime, timezone, timedelta

    since = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(
            func.count(Market.id).label("markets"),
            func.sum(Market.taker_fee_collected).label("taker"),
            func.sum(Market.resolution_fee_collected).label("resolution"),
            func.sum(Market.creator_fee_earned).label("creator"),
            func.count(Bet.id).label("bets"),
            func.sum(Bet.amount).label("volume"),
        )
        .select_from(Market)
        .outerjoin(Bet, Bet.market_id == Market.id)
        .where(Market.created_at >= since)
    )
    row = result.one()

    taker      = row.taker      or 0
    resolution = row.resolution or 0
    creator    = row.creator    or 0
    total_fee  = taker + resolution
    platform   = total_fee - creator

    return {
        "period_days":       days,
        "markets_created":   row.markets or 0,
        "total_bets":        row.bets    or 0,
        "total_volume_ton":  round((row.volume or 0) / NANO, 2),
        "revenue": {
            "total_ton":             round(total_fee  / NANO, 4),
            "platform_ton":          round(platform   / NANO, 4),
            "creators_earned_ton":   round(creator    / NANO, 4),
            "taker_fees_ton":        round(taker      / NANO, 4),
            "resolution_fees_ton":   round(resolution / NANO, 4),
        },
        "fee_structure": {
            "taker_fee_pct":         FEE.taker_bps / 100,
            "resolution_fee_pct":    FEE.resolution_bps / 100,
            "creator_share_pct":     FEE.creator_share_bps / 100,
            "free_bets_per_user":    FEE.free_bets_count,
        },
    }
