"""
app/clob.py  —  Central Limit Order Book (CLOB) engine
Точная реализация механики Polymarket для TON Pred.

Ключевые концепции:
  - Каждый рынок имеет два outcome token: YES и NO
  - YES_price + NO_price = 1.0 (всегда)
  - Пользователь покупает/продаёт шеры (shares) по текущей цене
  - Можно войти и выйти в любой момент до закрытия рынка
  - При разрешении: YES-шер = 1 TON, NO-шер = 0 TON
  - AMM-формула (LMSR) для автоматического маркет-мейкинга без order book

Почему LMSR, а не чистый CLOB:
  - CLOB требует маркет-мейкеров и deep liquidity
  - LMSR (Logarithmic Market Scoring Rule) работает без них
  - Цена автоматически меняется при каждой ставке
  - Это то что Polymarket использует под капотом для малоликвидных рынков

Формула LMSR:
  cost(q_yes, q_no) = b × ln(e^(q_yes/b) + e^(q_no/b))
  price_yes = e^(q_yes/b) / (e^(q_yes/b) + e^(q_no/b))

  где b — параметр ликвидности (больше b → более устойчивые цены)
"""

import math
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

NANO = 1_000_000_000


class Outcome(str, Enum):
    YES = "yes"
    NO  = "no"


@dataclass
class CLOBMarket:
    """
    Состояние CLOB-рынка с LMSR-маркет-мейкером.
    
    q_yes, q_no — количество выпущенных шеров
    b           — параметр ликвидности (в TON)
    """
    market_id:  str
    b:          float          # ликвидность (рекомендуется 100–1000 TON)
    q_yes:      float = 0.0    # шеры YES в обращении
    q_no:       float = 0.0    # шеры NO в обращении
    resolved:   bool  = False
    winner:     Optional[Outcome] = None

    @property
    def price_yes(self) -> float:
        """Текущая цена YES-шера (вероятность YES)."""
        return _lmsr_price(self.q_yes, self.q_no, self.b)

    @property
    def price_no(self) -> float:
        """Текущая цена NO-шера."""
        return 1.0 - self.price_yes

    @property
    def total_cost_ton(self) -> float:
        """Всего TON внесено в маркет-мейкер."""
        return _lmsr_cost(self.q_yes, self.q_no, self.b)


def _lmsr_cost(q_yes: float, q_no: float, b: float) -> float:
    """LMSR cost function: C(q) = b × ln(e^(q_yes/b) + e^(q_no/b))"""
    if b <= 0:
        return 0.0
    return b * math.log(math.exp(q_yes / b) + math.exp(q_no / b))


def _lmsr_price(q_yes: float, q_no: float, b: float) -> float:
    """Цена YES-шера = e^(q_yes/b) / (e^(q_yes/b) + e^(q_no/b))"""
    if b <= 0:
        return 0.5
    ey = math.exp(q_yes / b)
    en = math.exp(q_no  / b)
    return ey / (ey + en)


def cost_to_buy(
    market: CLOBMarket,
    outcome: Outcome,
    shares: float,
) -> float:
    """
    Стоимость покупки `shares` шеров указанного исхода в TON.
    Это разница функций стоимости до и после покупки.
    """
    cost_before = _lmsr_cost(market.q_yes, market.q_no, market.b)
    if outcome == Outcome.YES:
        cost_after = _lmsr_cost(market.q_yes + shares, market.q_no, market.b)
    else:
        cost_after = _lmsr_cost(market.q_yes, market.q_no + shares, market.b)
    return cost_after - cost_before


def cost_to_sell(
    market: CLOBMarket,
    outcome: Outcome,
    shares: float,
) -> float:
    """
    Выручка от продажи `shares` шеров в TON (всегда > 0 если shares > 0).
    """
    cost_before = _lmsr_cost(market.q_yes, market.q_no, market.b)
    if outcome == Outcome.YES:
        cost_after = _lmsr_cost(market.q_yes - shares, market.q_no, market.b)
    else:
        cost_after = _lmsr_cost(market.q_yes, market.q_no - shares, market.b)
    return cost_before - cost_after  # положительное значение


def shares_for_ton(
    market: CLOBMarket,
    outcome: Outcome,
    ton_amount: float,
    max_iter: int = 50,
) -> float:
    """
    Бинарный поиск: сколько шеров можно купить за `ton_amount` TON.
    """
    lo, hi = 0.0, ton_amount / max(0.001, market.price_yes if outcome == Outcome.YES else market.price_no) * 2
    for _ in range(max_iter):
        mid  = (lo + hi) / 2
        cost = cost_to_buy(market, outcome, mid)
        if abs(cost - ton_amount) < 1e-9:
            break
        if cost < ton_amount:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def buy_shares(
    market: CLOBMarket,
    outcome: Outcome,
    ton_amount: float,
) -> tuple[float, float]:
    """
    Покупка шеров за TON.
    Возвращает (shares_received, actual_cost_ton).
    Обновляет состояние рынка.
    """
    shares = shares_for_ton(market, outcome, ton_amount)
    actual_cost = cost_to_buy(market, outcome, shares)
    if outcome == Outcome.YES:
        market.q_yes += shares
    else:
        market.q_no  += shares
    return shares, actual_cost


def sell_shares(
    market: CLOBMarket,
    outcome: Outcome,
    shares: float,
) -> float:
    """
    Продажа шеров обратно маркет-мейкеру.
    Возвращает TON полученных.
    Обновляет состояние рынка.
    """
    if shares <= 0:
        return 0.0
    proceeds = cost_to_sell(market, outcome, shares)
    if outcome == Outcome.YES:
        market.q_yes = max(0.0, market.q_yes - shares)
    else:
        market.q_no  = max(0.0, market.q_no  - shares)
    return proceeds


def resolve_market(market: CLOBMarket, winner: Outcome) -> None:
    """Разрешение рынка."""
    market.resolved = True
    market.winner   = winner


def payout_for_shares(
    market: CLOBMarket,
    outcome: Outcome,
    shares: float,
) -> float:
    """
    Выплата при разрешении рынка.
    Победивший шер = 1 TON, проигравший = 0.
    """
    if not market.resolved:
        return 0.0
    if market.winner == outcome:
        return shares  # 1 TON за каждый шер
    return 0.0


# ── Price impact calculator ───────────────────────────────────────────────────

def price_impact(
    market: CLOBMarket,
    outcome: Outcome,
    ton_amount: float,
) -> dict:
    """
    Рассчитывает влияние покупки на цену (slippage).
    Используется для отображения в UI перед подтверждением ставки.
    """
    price_before = market.price_yes if outcome == Outcome.YES else market.price_no
    shares = shares_for_ton(market, outcome, ton_amount)
    cost   = cost_to_buy(market, outcome, shares)

    # Временно обновляем для расчёта новой цены
    import copy
    m2 = copy.copy(market)
    if outcome == Outcome.YES:
        m2.q_yes += shares
    else:
        m2.q_no  += shares
    price_after = m2.price_yes if outcome == Outcome.YES else m2.price_no

    avg_price = cost / shares if shares > 0 else price_before

    return {
        "shares":         round(shares, 6),
        "cost_ton":       round(cost, 6),
        "price_before":   round(price_before, 4),
        "price_after":    round(price_after, 4),
        "avg_price":      round(avg_price, 4),
        "price_impact":   round(abs(price_after - price_before), 4),
        "potential_win":  round(shares, 4),  # при победе = shares TON
        "implied_prob":   round(price_before * 100, 2),
    }


# ── Fee calculation для CLOB ──────────────────────────────────────────────────

def clob_taker_fee(
    ton_amount: float,
    current_price: float,
    category: str = "Other",
) -> float:
    """
    Динамическая fee по формуле Polymarket:
    fee = amount × theta × p × (1 - p)
    """
    THETA = {
        "Sports":   0.030,
        "Crypto":   0.060,
        "Politics": 0.035,
        "Weather":  0.040,
        "Other":    0.040,
    }
    theta = THETA.get(category, 0.040)
    p = max(0.01, min(0.99, current_price))
    return ton_amount * theta * p * (1 - p)
