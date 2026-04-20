from pydantic import BaseModel, Field, field_validator, computed_field
from typing import Optional
from datetime import datetime
from decimal import Decimal

from app.models import MarketStatus, MarketCategory, OracleType, BetOutcome, TxStatus

NANO = 1_000_000_000  # 1 TON in nanoTON


def nano_to_ton(nano: int) -> float:
    return round(nano / NANO, 4)


def ton_to_nano(ton: float) -> int:
    return int(Decimal(str(ton)) * NANO)


# ── Users ──────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    ton_address: Optional[str] = None


class UserUpdate(BaseModel):
    username: Optional[str] = None
    ton_address: Optional[str] = None

    @field_validator("ton_address")
    @classmethod
    def validate_ton_address(cls, v):
        if v and not (v.startswith("EQ") or v.startswith("UQ") or v.startswith("0:")):
            raise ValueError("Invalid TON address format")
        return v


class UserResponse(BaseModel):
    id: str
    telegram_id: int
    username: Optional[str]
    ton_address: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Markets ────────────────────────────────────────────────────────────────────

class MarketCreate(BaseModel):
    title: str = Field(..., min_length=5, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    category: MarketCategory = MarketCategory.OTHER
    oracle_type: OracleType = OracleType.CREATOR
    oracle_params: Optional[dict] = Field(
        None,
        description=(
            "Параметры оракула. Примеры:\n"
            "Крипто: {type:'crypto', symbol:'BTC/USD', condition:'above', target:100000}\n"
            "Спорт: {type:'sports', provider:'football_data', match_id:'459023', question:'home_win'}\n"
            "Погода: {type:'weather', city:'Moscow', date:'2026-05-20', question:'rain'}"
        )
    )
    bet_closes_at: datetime

    @field_validator("bet_closes_at")
    @classmethod
    def must_be_future(cls, v):
        if v <= datetime.now(v.tzinfo):
            raise ValueError("bet_closes_at must be in the future")
        return v


class MarketUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=5, max_length=255)
    description: Optional[str] = None
    status: Optional[MarketStatus] = None


class MarketResolve(BaseModel):
    winning_outcome: BetOutcome
    resolution_tx_hash: Optional[str] = None


class MarketResponse(BaseModel):
    id: str
    title: str
    description: Optional[str]
    category: MarketCategory
    status: MarketStatus
    oracle_type: OracleType
    contract_address: Optional[str]
    oracle_params: Optional[dict] = None

    # Pools in TON (human-readable)
    yes_pool_ton: float = Field(default=0.0)
    no_pool_ton: float = Field(default=0.0)
    total_pool_ton: float = Field(default=0.0)
    yes_pct: float = Field(default=50.0)
    no_pct: float = Field(default=50.0)

    bet_closes_at: datetime
    resolved_at: Optional[datetime]
    winning_outcome: Optional[BetOutcome]
    created_at: datetime

    creator_id: str
    bet_count: int = 0

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_market(cls, m, bet_count: int = 0):
        return cls(
            id=m.id,
            title=m.title,
            description=m.description,
            category=m.category,
            status=m.status,
            oracle_type=m.oracle_type,
            contract_address=m.contract_address,
            oracle_params=m.oracle_params,
            yes_pool_ton=nano_to_ton(m.yes_pool),
            no_pool_ton=nano_to_ton(m.no_pool),
            total_pool_ton=nano_to_ton(m.total_pool),
            yes_pct=m.yes_pct,
            no_pct=m.no_pct,
            bet_closes_at=m.bet_closes_at,
            resolved_at=m.resolved_at,
            winning_outcome=m.winning_outcome,
            created_at=m.created_at,
            creator_id=m.creator_id,
            bet_count=bet_count,
        )


# ── Bets ───────────────────────────────────────────────────────────────────────

class BetCreate(BaseModel):
    market_id: str
    outcome: BetOutcome
    amount_ton: float = Field(..., gt=0.1, description="Amount in TON (min 0.1)")
    tx_hash: Optional[str] = Field(None, description="On-chain tx hash for verification")

    @computed_field
    @property
    def amount_nano(self) -> int:
        return ton_to_nano(self.amount_ton)


class BetResponse(BaseModel):
    id: str
    market_id: str
    user_id: str
    outcome: BetOutcome
    amount_ton: float
    potential_payout_ton: Optional[float]
    tx_hash: Optional[str]
    tx_status: TxStatus
    is_winner: Optional[bool]
    payout_ton: Optional[float]
    claimed_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_bet(cls, b):
        return cls(
            id=b.id,
            market_id=b.market_id,
            user_id=b.user_id,
            outcome=b.outcome,
            amount_ton=nano_to_ton(b.amount),
            potential_payout_ton=nano_to_ton(b.potential_payout) if b.potential_payout else None,
            tx_hash=b.tx_hash,
            tx_status=b.tx_status,
            is_winner=b.is_winner,
            payout_ton=nano_to_ton(b.payout_amount) if b.payout_amount else None,
            claimed_at=b.claimed_at,
            created_at=b.created_at,
        )


# ── TON ────────────────────────────────────────────────────────────────────────

class TonConnectPayload(BaseModel):
    """Payload for TON Connect wallet verification"""
    address: str
    proof: dict  # TON Connect proof object
    network: str = "testnet"


class TonTxVerify(BaseModel):
    tx_hash: str
    expected_amount_nano: Optional[int] = None
    expected_destination: Optional[str] = None


class TonTxResponse(BaseModel):
    hash: str
    status: str
    amount: Optional[int]
    from_address: Optional[str]
    to_address: Optional[str]
    confirmed_at: Optional[datetime]


# ── Pagination ─────────────────────────────────────────────────────────────────

class PaginatedMarkets(BaseModel):
    items: list[MarketResponse]
    total: int
    page: int
    page_size: int
    has_next: bool
