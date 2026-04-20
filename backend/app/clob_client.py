"""
app/clob_client.py — HTTP-клиент к Go CLOB сервису.

Python FastAPI делегирует все CLOB-операции в Go:
  - Ценообразование (LMSR)
  - Подписание sell-ордеров (operator ed25519)
  - Формирование TON-транзакций

Это зеркало архитектуры Polymarket:
  Python = application layer (API, бизнес-логика, оракулы)
  Go     = protocol layer (CLOB, settlement, signing)
"""

from __future__ import annotations

import httpx
from app.config import settings


CLOB_BASE = getattr(settings, "CLOB_SERVICE_URL", "http://localhost:8081")


async def _post(path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{CLOB_BASE}{path}", json=body)
        r.raise_for_status()
        return r.json()


async def _get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{CLOB_BASE}{path}")
        r.raise_for_status()
        return r.json()


# ── Регистрация рынка (TokenRegistered аналог) ───────────────────────────

async def register_market(market_id: str, b_param: float, closes_at: int) -> dict:
    """Зарегистрировать рынок в Go-сервисе + получить register-payload."""
    return await _post("/markets/register", {
        "id": market_id,
        "b_param": b_param,
        "closes_at": closes_at,
    })


# ── Цена и price impact ───────────────────────────────────────────────────

async def get_price(market_id: str, outcome: int, amount_nanos: float = 0) -> dict:
    """Текущая цена YES/NO + price impact для amount_nanos."""
    return await _post(f"/markets/{market_id}/price", {
        "outcome": outcome,
        "amount_nanos": amount_nanos,
    })


# ── Покупка шеров (fill_order) ────────────────────────────────────────────

async def process_buy(
    market_id: str,
    sender_addr_hex: str,
    outcome: int,
    amount_nanos: float,
    min_shares: int = 0,
) -> dict:
    """
    Обработать покупку в Go-LMSR.
    Возвращает: shares, cost_nanos, price_impact, tx_payload (hex для TON).

    Python затем отправляет tx_payload пользователю — тот кладёт его
    в тело TON-транзакции op 0x10 fill_order.
    """
    return await _post(f"/markets/{market_id}/buy", {
        "sender_addr_hex": sender_addr_hex,
        "outcome": outcome,
        "amount_nanos": amount_nanos,
        "min_shares": min_shares,
    })


# ── Продажа шеров (match_orders) ─────────────────────────────────────────

async def process_sell(
    market_id: str,
    sender_addr_hex: str,
    outcome: int,
    shares: float,
) -> dict:
    """
    Обработать продажу в Go-LMSR.

    Go-сервис:
      1. Считает LMSR-цену (proceeds_nanos)
      2. Подписывает ордер ed25519 (sig_hex)
      3. Формирует tx_payload (op 0x12 match_orders)

    Python возвращает tx_payload клиенту.
    Клиент отправляет его в CTFExchange.fc — контракт верифицирует подпись
    и выплачивает TON.

    Аналог Polymarket: operator подписывает Order → вызывает matchOrders().
    """
    return await _post(f"/markets/{market_id}/sell", {
        "sender_addr_hex": sender_addr_hex,
        "outcome": outcome,
        "shares": shares,
    })


# ── Разрешение рынка (resolve) ────────────────────────────────────────────

async def resolve_market(
    market_id: str,
    outcome: int,
    sender_addr_hex: str,
    closes_at: int,
) -> dict:
    """
    Разрешить рынок оракулом.
    Go подписывает resolve → Python отправляет tx в CTFExchange.fc op 0x13.
    """
    return await _post(f"/markets/{market_id}/resolve", {
        "outcome": outcome,
        "sender_addr_hex": sender_addr_hex,
        "closes_at": closes_at,
    })


# ── Состояние рынка ───────────────────────────────────────────────────────

async def get_market_state(market_id: str) -> dict:
    """Текущее состояние рынка из Go-сервиса."""
    return await _get(f"/markets/{market_id}")


async def get_operator_pubkey() -> str:
    """Публичный ключ оператора — должен совпадать с контрактом."""
    data = await _get("/operator/pubkey")
    return data["pubkey_hex"]
