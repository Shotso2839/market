"""
app/fees.py  —  Dynamic fee engine (Polymarket-style)

Формула Polymarket:
    fee = amount × theta × p × (1 - p)

где:
    amount  — сумма ставки в nanoTON
    theta   — коэффициент категории (наш, чуть ниже Polymarket)
    p       — текущая вероятность YES (0.0–1.0)

Свойства кривой:
    • Максимум при p = 0.50  (самый неопределённый рынок)
    • Стремится к 0 при p → 0 или p → 1 (очевидный исход)
    • Симметрична относительно 50%

Наши theta vs Polymarket theta:
    Категория   | Наш theta | PM theta | Наш пик  | PM пик
    ------------+-----------+----------+----------+--------
    Спорт       |  0.030    |  0.030   |  0.75%   |  0.75%
    Крипто      |  0.060    |  0.072   |  1.50%   |  1.80%
    Политика    |  0.035    |  0.040   |  0.875%  |  1.00%
    Погода      |  0.040    |  0.050   |  1.00%   |  1.25%
    Другое      |  0.040    |  0.050   |  1.00%   |  1.25%

Мы везде НИЖЕ Polymarket — это наше конкурентное преимущество.

Resolution fee при выплате — убираем, заменяем полностью динамическим taker fee.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

NANO = 1_000_000_000  # 1 TON = 1_000_000_000 nanoTON
MIN_FEE_NANO = 100_000  # 0.0001 TON — минимальная комиссия


class MarketCategory(str, Enum):
    SPORTS   = "Sports"
    CRYPTO   = "Crypto"
    POLITICS = "Politics"
    WEATHER  = "Weather"
    OTHER    = "Other"


# ── Theta-коэффициенты по категориям ──────────────────────────────────────────
# Наши значения ~15–20% ниже Polymarket — это явное конкурентное преимущество
CATEGORY_THETA: dict[str, float] = {
    MarketCategory.SPORTS:   0.030,   # пик 0.75% при 50/50
    MarketCategory.CRYPTO:   0.060,   # пик 1.50% при 50/50
    MarketCategory.POLITICS: 0.035,   # пик 0.875% при 50/50
    MarketCategory.WEATHER:  0.040,   # пик 1.00% при 50/50
    MarketCategory.OTHER:    0.040,   # пик 1.00% при 50/50
}
DEFAULT_THETA = 0.040


# ── Доли распределения комиссии ───────────────────────────────────────────────
CREATOR_SHARE  = 0.20   # 20% → создателю рынка
PLATFORM_SHARE = 0.80   # 80% → платформе


# ── Основная функция расчёта комиссии ─────────────────────────────────────────

def dynamic_taker_fee(
    amount_nano: int,
    yes_probability: float,
    category: str = MarketCategory.OTHER,
    user_bet_count: int = 0,
    free_bets: int = 1,
) -> "BetFeeResult":
    """
    Рассчитывает динамическую taker fee по модели Polymarket.

    Args:
        amount_nano:      сумма ставки в nanoTON
        yes_probability:  текущая вероятность YES (0.01–0.99)
        category:         категория рынка
        user_bet_count:   сколько ставок пользователь уже сделал
        free_bets:        первые N ставок бесплатно (onboarding бонус)

    Returns:
        BetFeeResult
    """
    is_free = user_bet_count < free_bets

    if is_free or amount_nano <= 0:
        return BetFeeResult(
            gross_amount=amount_nano,
            fee_nano=0,
            net_amount=amount_nano,
            fee_ton=0.0,
            net_ton=round(amount_nano / NANO, 4),
            platform_share=0,
            creator_share=0,
            is_free=is_free,
            yes_probability=yes_probability,
            effective_rate_pct=0.0,
            category=category,
        )

    # Зажимаем вероятность чтобы избежать деления на 0
    p = max(0.01, min(0.99, yes_probability))
    theta = CATEGORY_THETA.get(category, DEFAULT_THETA)

    # Формула Polymarket: fee = amount × theta × p × (1 - p)
    fee_raw = amount_nano * theta * p * (1 - p)
    fee = max(0, int(fee_raw))

    # Применяем минимальный порог
    if fee < MIN_FEE_NANO:
        fee = 0

    net = amount_nano - fee
    platform = int(fee * PLATFORM_SHARE)
    creator  = fee - platform  # остаток чтобы суммы сходились

    eff_rate = (fee / amount_nano * 100) if amount_nano > 0 else 0.0

    return BetFeeResult(
        gross_amount=amount_nano,
        fee_nano=fee,
        net_amount=net,
        fee_ton=round(fee / NANO, 6),
        net_ton=round(net / NANO, 4),
        platform_share=platform,
        creator_share=creator,
        is_free=False,
        yes_probability=yes_probability,
        effective_rate_pct=round(eff_rate, 4),
        category=category,
    )


# ── Пиковая ставка по категории (для отображения в UI) ───────────────────────

def calculate_bet_fee(
    amount_nano: int,
    user_bet_count: int = 0,
    yes_probability: float = 0.5,
    category: str = MarketCategory.OTHER,
    free_bets: int | None = None,
) -> "BetFeeResult":
    """
    Backward-compatible wrapper for callers that still use the legacy fee API.

    If the caller does not pass a live market probability yet, we assume a
    neutral 50/50 market and reuse the dynamic taker-fee engine underneath.
    """
    return dynamic_taker_fee(
        amount_nano=amount_nano,
        yes_probability=yes_probability,
        category=category,
        user_bet_count=user_bet_count,
        free_bets=FEE.free_bets_count if free_bets is None else free_bets,
    )


def peak_fee_pct(category: str = MarketCategory.OTHER) -> float:
    """
    Максимальная комиссия для категории (при p=0.50).
    fee_max = theta × 0.5 × 0.5 = theta × 0.25
    """
    theta = CATEGORY_THETA.get(category, DEFAULT_THETA)
    return round(theta * 0.25 * 100, 3)


def fee_at_probability(
    amount_nano: int,
    probability: float,
    category: str = MarketCategory.OTHER,
) -> int:
    """Быстрый расчёт комиссии без создания полного BetFeeResult."""
    p     = max(0.01, min(0.99, probability))
    theta = CATEGORY_THETA.get(category, DEFAULT_THETA)
    return max(0, int(amount_nano * theta * p * (1 - p)))


# ── Датаклассы ────────────────────────────────────────────────────────────────

@dataclass
class BetFeeResult:
    gross_amount:       int
    fee_nano:           int
    net_amount:         int
    fee_ton:            float
    net_ton:            float
    platform_share:     int
    creator_share:      int
    is_free:            bool
    yes_probability:    float
    effective_rate_pct: float
    category:           str


@dataclass
class FeeConfig:
    """Обратная совместимость для старого кода."""
    taker_bps:          int   = 50
    resolution_bps:     int   = 0     # убираем resolution fee
    creator_share_bps:  int   = 2000
    free_bets_count:    int   = 1
    min_fee_nano:       int   = MIN_FEE_NANO

    def split_fee(self, fee_nano: int) -> tuple[int, int]:
        creator  = int(fee_nano * self.creator_share_bps / 10000)
        platform = fee_nano - creator
        return platform, creator


FEE = FeeConfig()


# ── Расчёт выплаты победителям (без резолюшн-фи) ────────────────────────────

def calculate_winner_payout(
    bet_net_amount: int,
    winning_pool: int,
    total_pool: int,
) -> int:
    """
    Паримьютельный расчёт выплаты.
    Вся комиссия уже собрана при ставке (taker fee).
    При разрешении рынка комиссия не берётся — весь пул идёт победителям.

    payout = (bet_net / winning_pool) × total_pool
    """
    if winning_pool <= 0 or total_pool <= 0:
        return 0
    return int(bet_net_amount / winning_pool * total_pool)


def calculate_resolution_fee(yes_pool: int, no_pool: int) -> "ResolutionFeeResult":
    """
    Заглушка для обратной совместимости.
    Resolution fee = 0 в новой модели — вся комиссия берётся при ставке.
    """
    total = yes_pool + no_pool
    return ResolutionFeeResult(
        total_pool=total,
        fee_nano=0,
        distributable=total,
        platform_share=0,
        creator_share=0,
        fee_pct=0.0,
    )


@dataclass
class ResolutionFeeResult:
    total_pool:    int
    fee_nano:      int
    distributable: int
    platform_share:int
    creator_share: int
    fee_pct:       float


# ── Revenue summary helpers ───────────────────────────────────────────────────

@dataclass
class RevenueEvent:
    event_type:    str
    market_id:     str
    amount_nano:   int
    platform_nano: int
    creator_nano:  int
    user_id:       Optional[str] = None


def summarize_revenue(events: list[RevenueEvent]) -> dict:
    total    = sum(e.amount_nano   for e in events)
    platform = sum(e.platform_nano for e in events)
    creator  = sum(e.creator_nano  for e in events)
    taker_t  = sum(e.amount_nano   for e in events if e.event_type == 'taker_fee')
    res_t    = sum(e.amount_nano   for e in events if e.event_type == 'resolution_fee')
    return {
        "total_revenue_ton":     round(total    / NANO, 4),
        "platform_revenue_ton":  round(platform / NANO, 4),
        "creator_revenue_ton":   round(creator  / NANO, 4),
        "taker_fee_revenue_ton": round(taker_t  / NANO, 4),
        "resolution_fee_ton":    round(res_t    / NANO, 4),
        "events_count":          len(events),
    }
