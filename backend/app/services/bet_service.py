from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from typing import Optional
from datetime import datetime, timezone

from app.models import Bet, Market, MarketStatus, TxStatus, BetOutcome
from app.schemas import BetCreate


class BetService:

    def _calculate_potential_payout(
        self,
        amount: int,
        outcome: BetOutcome,
        market: Market,
        fee_bps: int = 200,
    ) -> int:
        """
        Pari-mutuel payout estimate at time of bet.
        payout = (amount / new_winning_pool) * new_total_pool * (1 - fee)
        """
        new_yes = market.yes_pool + (amount if outcome == BetOutcome.YES else 0)
        new_no = market.no_pool + (amount if outcome == BetOutcome.NO else 0)
        total = new_yes + new_no
        win_pool = new_yes if outcome == BetOutcome.YES else new_no

        if win_pool == 0:
            return amount

        distributable = int(total * (10000 - fee_bps) / 10000)
        return int((amount / win_pool) * distributable)

    async def place_bet(
        self,
        db: AsyncSession,
        data: BetCreate,
        user_id: str,
        market: Market,
    ) -> Bet:
        if market.status != MarketStatus.OPEN:
            raise ValueError("Market is not open for betting")

        if market.bet_closes_at <= datetime.now(timezone.utc):
            raise ValueError("Betting period has closed")

        # ── Fee calculation ───────────────────────────────────────────────────
        from app.fees import calculate_bet_fee, FEE
        user_bet_count = await self._get_user_bet_count(db, user_id)
        fee_result = calculate_bet_fee(data.amount_nano, user_bet_count)

        # net_amount goes into the pool; fee is split between platform & creator
        net_for_pool = fee_result.net_amount

        potential = self._calculate_potential_payout(
            net_for_pool, data.outcome, market
        )

        bet = Bet(
            market_id=data.market_id,
            user_id=user_id,
            outcome=data.outcome,
            amount=data.amount_nano,       # gross amount (what user sent)
            net_amount=net_for_pool,       # net amount (what enters pool)
            fee_nano=fee_result.fee_nano,
            is_free_bet=fee_result.is_free,
            potential_payout=potential,
            tx_hash=data.tx_hash,
            tx_status=TxStatus.PENDING,
        )
        db.add(bet)

        # Update market pools with NET amount (after taker fee)
        if data.outcome == BetOutcome.YES:
            market.yes_pool += net_for_pool
        else:
            market.no_pool += net_for_pool

        # Track taker fee on market
        market.taker_fee_collected = (market.taker_fee_collected or 0) + fee_result.fee_nano
        market.creator_fee_earned  = (market.creator_fee_earned  or 0) + fee_result.creator_share

        db.add(market)
        await db.flush()
        return bet

    async def _get_user_bet_count(self, db: AsyncSession, user_id: str) -> int:
        """How many confirmed bets this user has already made."""
        from sqlalchemy import func
        result = await db.execute(
            select(func.count(Bet.id)).where(
                Bet.user_id == user_id,
                Bet.tx_status != TxStatus.FAILED,
            )
        )
        return result.scalar_one() or 0

    async def confirm_bet_tx(
        self,
        db: AsyncSession,
        bet: Bet,
        tx_hash: str,
    ) -> Bet:
        bet.tx_hash = tx_hash
        bet.tx_status = TxStatus.CONFIRMED
        db.add(bet)
        return bet

    async def fail_bet_tx(self, db: AsyncSession, bet: Bet) -> Bet:
        """Mark bet as failed; reverse pool update."""
        if bet.tx_status == TxStatus.FAILED:
            return bet

        bet.tx_status = TxStatus.FAILED
        db.add(bet)

        # Reverse pool
        market_result = await db.execute(
            select(Market).where(Market.id == bet.market_id)
        )
        market = market_result.scalar_one_or_none()
        if market:
            if bet.outcome == BetOutcome.YES:
                market.yes_pool = max(0, market.yes_pool - bet.amount)
            else:
                market.no_pool = max(0, market.no_pool - bet.amount)
            db.add(market)

        return bet

    async def get_user_bets(
        self,
        db: AsyncSession,
        user_id: str,
        market_id: Optional[str] = None,
    ) -> list[Bet]:
        filters = [Bet.user_id == user_id]
        if market_id:
            filters.append(Bet.market_id == market_id)
        result = await db.execute(
            select(Bet)
            .where(and_(*filters))
            .order_by(Bet.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_market_bets(
        self,
        db: AsyncSession,
        market_id: str,
    ) -> list[Bet]:
        result = await db.execute(
            select(Bet)
            .where(Bet.market_id == market_id)
            .order_by(Bet.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_user_stats(
        self,
        db: AsyncSession,
        user_id: str,
    ) -> dict:
        total_bets = await db.execute(
            select(func.count(Bet.id)).where(Bet.user_id == user_id)
        )
        won_bets = await db.execute(
            select(func.count(Bet.id)).where(
                and_(Bet.user_id == user_id, Bet.is_winner == True)
            )
        )
        total_wagered = await db.execute(
            select(func.sum(Bet.amount)).where(Bet.user_id == user_id)
        )
        total_won = await db.execute(
            select(func.sum(Bet.payout_amount)).where(
                and_(Bet.user_id == user_id, Bet.is_winner == True)
            )
        )
        return {
            "total_bets": total_bets.scalar_one() or 0,
            "won_bets": won_bets.scalar_one() or 0,
            "total_wagered_nano": total_wagered.scalar_one() or 0,
            "total_won_nano": total_won.scalar_one() or 0,
        }


bet_service = BetService()
