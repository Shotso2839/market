from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas import BetCreate, BetResponse
from app.services.bet_service import bet_service
from app.services.market_service import market_service
from app.services.ton_service import ton_service
from app.websocket import emit_new_bet, emit_bet_confirmed

router = APIRouter()


@router.post("", response_model=BetResponse, status_code=status.HTTP_201_CREATED)
async def place_bet(
    data: BetCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Place a bet on a market outcome.

    Flow:
    1. Client sends TON to the smart contract directly from their wallet
    2. Client calls this endpoint with the resulting tx_hash
    3. Backend verifies the tx on-chain and records the bet
    """
    if not current_user.ton_address:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connect a TON wallet to place bets",
        )

    market = await market_service.get_by_id(db, data.market_id)
    if not market:
        raise HTTPException(status_code=404, detail="Market not found")

    # If tx_hash provided, verify on-chain before recording
    if data.tx_hash and market.contract_address:
        tx_result = await ton_service.verify_transaction(
            tx_hash=data.tx_hash,
            expected_destination=market.contract_address,
            expected_amount_nano=data.amount_nano,
        )
        if not tx_result["confirmed"]:
            raise HTTPException(
                status_code=400,
                detail=f"Transaction not confirmed: {tx_result.get('reason', 'unknown')}",
            )

    try:
        bet = await bet_service.place_bet(db, data, current_user.id, market)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Fire WebSocket update to market room (non-blocking)
    import asyncio
    asyncio.create_task(emit_new_bet(bet, market))

    # Telegram DM to bettor
    if current_user.telegram_id:
        from app.routers.telegram import notify_bet_placed
        asyncio.create_task(notify_bet_placed(
            current_user.telegram_id,
            market.title,
            data.outcome.value,
            data.amount_ton,
        ))

    return BetResponse.from_orm_bet(bet)


@router.get("/my", response_model=list[BetResponse])
async def get_my_bets(
    market_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current user's bet history."""
    bets = await bet_service.get_user_bets(db, current_user.id, market_id)
    return [BetResponse.from_orm_bet(b) for b in bets]


@router.get("/my/stats")
async def get_my_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated stats for the current user."""
    from app.schemas import nano_to_ton
    stats = await bet_service.get_user_stats(db, current_user.id)
    return {
        "total_bets": stats["total_bets"],
        "won_bets": stats["won_bets"],
        "win_rate": round(stats["won_bets"] / stats["total_bets"] * 100, 1) if stats["total_bets"] else 0,
        "total_wagered_ton": nano_to_ton(stats["total_wagered_nano"]),
        "total_won_ton": nano_to_ton(stats["total_won_nano"]),
        "net_ton": nano_to_ton(stats["total_won_nano"] - stats["total_wagered_nano"]),
    }


@router.get("/market/{market_id}", response_model=list[BetResponse])
async def get_market_bets(
    market_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get all bets for a specific market (public)."""
    bets = await bet_service.get_market_bets(db, market_id)
    return [BetResponse.from_orm_bet(b) for b in bets]


@router.post("/{bet_id}/confirm")
async def confirm_bet_transaction(
    bet_id: str,
    tx_hash: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually confirm a bet transaction after on-chain verification."""
    from sqlalchemy import select
    from app.models import Bet

    result = await db.execute(select(Bet).where(Bet.id == bet_id))
    bet = result.scalar_one_or_none()

    if not bet:
        raise HTTPException(status_code=404, detail="Bet not found")
    if bet.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Verify on chain
    tx = await ton_service.verify_transaction(tx_hash)
    if not tx["confirmed"]:
        raise HTTPException(status_code=400, detail="Transaction not confirmed on chain")

    bet = await bet_service.confirm_bet_tx(db, bet, tx_hash)

    # Notify bettor via WS personal channel
    import asyncio
    asyncio.create_task(emit_bet_confirmed(bet, current_user.id))

    return {"status": "confirmed", "tx_hash": tx_hash}
