from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, update
from sqlalchemy.orm import selectinload
from typing import Optional
from datetime import datetime, timezone

from app.models import Market, Bet, MarketStatus, BetOutcome
from app.schemas import MarketCreate, MarketResolve, nano_to_ton
from app.config import settings


class MarketService:

    async def create(
        self,
        db: AsyncSession,
        data: MarketCreate,
        creator_id: str,
    ) -> Market:
        market = Market(
            creator_id=creator_id,
            title=data.title,
            description=data.description,
            category=data.category,
            oracle_type=data.oracle_type,
            oracle_params=getattr(data, 'oracle_params', None),
            bet_closes_at=data.bet_closes_at,
            status=MarketStatus.OPEN,
        )
        db.add(market)
        await db.flush()
        return market

    async def get_by_id(
        self,
        db: AsyncSession,
        market_id: str,
    ) -> Optional[Market]:
        result = await db.execute(
            select(Market)
            .where(Market.id == market_id)
            .options(selectinload(Market.creator))
        )
        return result.scalar_one_or_none()

    async def list_markets(
        self,
        db: AsyncSession,
        status: Optional[MarketStatus] = None,
        category: Optional[str] = None,
        creator_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Market], int]:
        query = select(Market)
        count_query = select(func.count(Market.id))

        filters = []
        if status:
            filters.append(Market.status == status)
        if category:
            filters.append(Market.category == category)
        if creator_id:
            filters.append(Market.creator_id == creator_id)

        if filters:
            query = query.where(and_(*filters))
            count_query = count_query.where(and_(*filters))

        total_result = await db.execute(count_query)
        total = total_result.scalar_one()

        query = (
            query
            .order_by(Market.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await db.execute(query)
        markets = result.scalars().all()

        return list(markets), total

    async def get_bet_counts(
        self,
        db: AsyncSession,
        market_ids: list[str],
    ) -> dict[str, int]:
        if not market_ids:
            return {}
        result = await db.execute(
            select(Bet.market_id, func.count(Bet.id))
            .where(Bet.market_id.in_(market_ids))
            .group_by(Bet.market_id)
        )
        return {row[0]: row[1] for row in result.fetchall()}

    async def close_expired(self, db: AsyncSession) -> int:
        """Close markets whose betting period has expired. Call via cron."""
        now = datetime.now(timezone.utc)
        result = await db.execute(
            update(Market)
            .where(
                and_(
                    Market.status == MarketStatus.OPEN,
                    Market.bet_closes_at <= now,
                )
            )
            .values(status=MarketStatus.CLOSED)
            .returning(Market.id)
        )
        closed = result.fetchall()
        return len(closed)

    async def resolve(
        self,
        db: AsyncSession,
        market: Market,
        data: MarketResolve,
    ) -> Market:
        """
        Resolve market: set winner, calculate payouts for all bets.
        In production this should also trigger smart contract resolution tx.
        """
        if market.status not in (MarketStatus.OPEN, MarketStatus.CLOSED):
            raise ValueError(f"Cannot resolve market in status: {market.status}")

        market.status = MarketStatus.RESOLVED
        market.winning_outcome = data.winning_outcome
        market.resolved_at = datetime.now(timezone.utc)
        if data.resolution_tx_hash:
            market.resolution_tx_hash = data.resolution_tx_hash

        # Calculate payouts for all bets
        winning_pool = (
            market.yes_pool if data.winning_outcome == BetOutcome.YES
            else market.no_pool
        )
        total_pool = market.total_pool
        from app.fees import calculate_resolution_fee
        fee_result = calculate_resolution_fee(market.yes_pool, market.no_pool)
        distributable = fee_result.distributable

        # Record resolution fee on market
        market.resolution_fee_collected = (market.resolution_fee_collected or 0) + fee_result.fee_nano
        market.creator_fee_earned = (market.creator_fee_earned or 0) + fee_result.creator_share

        if winning_pool > 0:
            # Update all bets in one query per outcome
            bets_result = await db.execute(
                select(Bet).where(Bet.market_id == market.id)
            )
            bets = bets_result.scalars().all()

            for bet in bets:
                if bet.outcome == data.winning_outcome:
                    payout = int((bet.amount / winning_pool) * distributable)
                    bet.is_winner = True
                    bet.payout_amount = payout
                else:
                    bet.is_winner = False
                    bet.payout_amount = 0

        db.add(market)
        return market

    async def set_contract_address(
        self,
        db: AsyncSession,
        market: Market,
        contract_address: str,
        deploy_tx_hash: Optional[str] = None,
    ) -> Market:
        market.contract_address = contract_address
        if deploy_tx_hash:
            market.deploy_tx_hash = deploy_tx_hash
        db.add(market)
        return market


market_service = MarketService()
