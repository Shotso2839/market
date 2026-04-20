"""
Добавляем в models.py:
  - FeeEvent    — запись каждого fee-события
  - UserStats   — кеш статистики пользователя (bets_count для free_bets)
  - RevenueLog  — суммарная выручка платформы по дням

Добавляем в Market:
  - taker_fee_collected   — сумма собранных taker-комиссий (nanoTON)
  - resolution_fee_collected

Добавляем в Bet:
  - fee_nano              — уплаченная taker-комиссия
  - net_amount            — сумма ставки после taker-fee (идёт в пул)
  - is_free_bet           — True если первая ставка (без комиссии)
"""

# Этот файл — патч для models.py
# Применяется через patch_models() ниже

MODELS_PATCH = {
    # Новые колонки в таблице bets
    "bets_new_columns": """
    fee_nano = Column(BigInteger, nullable=False, default=0)
    net_amount = Column(BigInteger, nullable=True)  # amount - fee
    is_free_bet = Column(Boolean, nullable=False, default=False)
    """,

    # Новые колонки в таблице markets
    "markets_new_columns": """
    taker_fee_collected = Column(BigInteger, nullable=False, default=0)
    resolution_fee_collected = Column(BigInteger, nullable=False, default=0)
    creator_fee_earned = Column(BigInteger, nullable=False, default=0)
    """,
}
