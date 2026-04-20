from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, MarketStatus, MarketCategory
from app.schemas import MarketCreate, MarketResolve, MarketResponse, PaginatedMarkets
from app.services.market_service import market_service
router = APIRouter()


@router.get("", response_model=PaginatedMarkets)
async def list_markets(
    status: Optional[MarketStatus] = Query(None),
    category: Optional[MarketCategory] = Query(None),
    creator_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List prediction markets with optional filters."""
    markets, total = await market_service.list_markets(
        db,
        status=status,
        category=category.value if category else None,
        creator_id=creator_id,
        page=page,
        page_size=page_size,
    )

    market_ids = [m.id for m in markets]
    bet_counts = await market_service.get_bet_counts(db, market_ids)

    items = [
        MarketResponse.from_orm_market(m, bet_counts.get(m.id, 0))
        for m in markets
    ]

    return PaginatedMarkets(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_next=(page * page_size) < total,
    )


@router.post("", response_model=MarketResponse, status_code=status.HTTP_201_CREATED)
async def create_market(
    data: MarketCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new prediction market."""
    if not current_user.ton_address:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect a TON wallet before creating a market",
        )

    market = await market_service.create(db, data, creator_id=current_user.id)
    return MarketResponse.from_orm_market(market, 0)


@router.get("/{market_id}", response_model=MarketResponse)
async def get_market(
    market_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a single market by ID."""
    market = await market_service.get_by_id(db, market_id)
    if not market:
        raise HTTPException(status_code=404, detail="Market not found")

    counts = await market_service.get_bet_counts(db, [market_id])
    return MarketResponse.from_orm_market(market, counts.get(market_id, 0))


@router.post("/{market_id}/resolve", response_model=MarketResponse)
async def resolve_market(
    market_id: str,
    data: MarketResolve,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Resolve a market (declare the winning outcome).
    Only the market creator can resolve (unless oracle_type != 'self').
    """
    market = await market_service.get_by_id(db, market_id)
    if not market:
        raise HTTPException(status_code=404, detail="Market not found")

    if market.creator_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the market creator can resolve this market",
        )

    try:
        market = await market_service.resolve(db, market, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Broadcast to all watchers of this market
    import asyncio
    from app.websocket import emit_market_resolved
    asyncio.create_task(emit_market_resolved(market))

    # Notify every bettor via Telegram DM
    from app.routers.telegram import notify_market_resolved
    from sqlalchemy import select
    from app.models import Bet
    bets_result = await db.execute(
        select(Bet).where(Bet.market_id == market_id)
    )
    all_bets = bets_result.scalars().all()
    for bet in all_bets:
        if bet.user and bet.user.telegram_id:
            asyncio.create_task(notify_market_resolved(
                bet.user.telegram_id,
                market.title,
                data.winning_outcome.value,
                bet.is_winner or False,
                round(bet.payout_amount / 1_000_000_000, 4) if bet.payout_amount else 0.0,
            ))

    counts = await market_service.get_bet_counts(db, [market_id])
    return MarketResponse.from_orm_market(market, counts.get(market_id, 0))


@router.patch("/{market_id}/contract", response_model=MarketResponse)
async def set_contract_address(
    market_id: str,
    contract_address: str,
    deploy_tx_hash: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Link a deployed smart contract address to a market."""
    market = await market_service.get_by_id(db, market_id)
    if not market:
        raise HTTPException(status_code=404, detail="Market not found")
    if market.creator_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    market = await market_service.set_contract_address(
        db, market, contract_address, deploy_tx_hash
    )
    return MarketResponse.from_orm_market(market, 0)
