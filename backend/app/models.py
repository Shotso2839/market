from sqlalchemy import (  # noqa
    Column, String, Integer, BigInteger, Numeric, Boolean, JSON,
    DateTime, ForeignKey, Enum as SAEnum, Text, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
import uuid

from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class MarketStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"       # Betting period ended, awaiting resolution
    RESOLVED = "resolved"   # Winner declared, payouts available
    CANCELLED = "cancelled" # Refunds issued


class MarketCategory(str, enum.Enum):
    SPORT = "Спорт"
    CRYPTO = "Крипто"
    POLITICS = "Политика"
    WEATHER = "Погода"
    OTHER = "Другое"


class OracleType(str, enum.Enum):
    CREATOR = "self"
    PYTH    = "pyth"
    SPORTS  = "sports"
    WEATHER = "weather"
    DAO     = "dao"


class BetOutcome(str, enum.Enum):
    YES = "yes"
    NO = "no"


class TxStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"


# ── Users ──────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(64), nullable=True)
    ton_address = Column(String(68), unique=True, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    markets = relationship("Market", back_populates="creator")
    bets = relationship("Bet", back_populates="user")

    def __repr__(self):
        return f"<User tg={self.telegram_id} addr={self.ton_address}>"


# ── Markets ────────────────────────────────────────────────────────────────────

class Market(Base):
    __tablename__ = "markets"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    creator_id = Column(String(36), ForeignKey("users.id"), nullable=False)

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(SAEnum(MarketCategory), nullable=False, default=MarketCategory.OTHER)
    status = Column(SAEnum(MarketStatus), nullable=False, default=MarketStatus.OPEN, index=True)

    oracle_type = Column(SAEnum(OracleType), nullable=False, default=OracleType.CREATOR)
    # JSON-поле с параметрами оракула. Структура зависит от oracle_type:
    # crypto: {"type":"crypto","symbol":"BTC/USD","condition":"above","target":100000}
    # sports: {"type":"sports","provider":"football_data","match_id":"459023","question":"home_win"}
    # weather: {"type":"weather","city":"Moscow","date":"2026-05-20","question":"rain"}
    oracle_params = Column(JSON, nullable=True)

    # TON blockchain data
    contract_address = Column(String(68), nullable=True, unique=True)
    deploy_tx_hash = Column(String(64), nullable=True)

    # Pools (in nanoTON for precision, 1 TON = 1_000_000_000 nanoTON)
    yes_pool = Column(BigInteger, nullable=False, default=0)
    no_pool = Column(BigInteger, nullable=False, default=0)

    # Fee accounting (nanoTON)
    taker_fee_collected    = Column(BigInteger, nullable=False, default=0)
    resolution_fee_collected = Column(BigInteger, nullable=False, default=0)
    creator_fee_earned     = Column(BigInteger, nullable=False, default=0)

    # Timestamps
    bet_closes_at = Column(DateTime(timezone=True), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Resolution
    winning_outcome = Column(SAEnum(BetOutcome), nullable=True)
    resolution_tx_hash = Column(String(64), nullable=True)

    creator = relationship("User", back_populates="markets")
    bets = relationship("Bet", back_populates="market", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_markets_status_closes", "status", "bet_closes_at"),
        Index("ix_markets_category", "category"),
    )

    @property
    def total_pool(self) -> int:
        return self.yes_pool + self.no_pool

    @property
    def yes_pct(self) -> float:
        if self.total_pool == 0:
            return 50.0
        return round(self.yes_pool / self.total_pool * 100, 1)

    @property
    def no_pct(self) -> float:
        return round(100.0 - self.yes_pct, 1)


# ── Bets ───────────────────────────────────────────────────────────────────────

class Bet(Base):
    __tablename__ = "bets"

    id = Column(String(36), primary_key=True, default=gen_uuid)
    market_id = Column(String(36), ForeignKey("markets.id"), nullable=False)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)

    outcome = Column(SAEnum(BetOutcome), nullable=False)
    amount = Column(BigInteger, nullable=False)          # nanoTON
    potential_payout = Column(BigInteger, nullable=True) # calculated at bet time

    # Fee tracking
    fee_nano = Column(BigInteger, nullable=False, default=0)
    net_amount = Column(BigInteger, nullable=True)   # amount - fee (goes into pool)
    is_free_bet = Column(Boolean, nullable=False, default=False)

    # On-chain proof
    tx_hash = Column(String(64), nullable=True, unique=True)
    tx_status = Column(SAEnum(TxStatus), nullable=False, default=TxStatus.PENDING)

    # Payout tracking
    is_winner = Column(Boolean, nullable=True)           # set after resolution
    payout_amount = Column(BigInteger, nullable=True)    # actual payout in nanoTON
    payout_tx_hash = Column(String(64), nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    market = relationship("Market", back_populates="bets")
    user = relationship("User", back_populates="bets")

    __table_args__ = (
        Index("ix_bets_market_user", "market_id", "user_id"),
        Index("ix_bets_tx_hash", "tx_hash"),
    )
