"""
Telegram Bot Webhook

Set webhook URL:
  POST https://api.telegram.org/bot{TOKEN}/setWebhook
       ?url=https://yourdomain.com/api/v1/telegram/webhook

Supported commands:
  /start           — welcome + Mini App button
  /markets         — list top 5 open markets
  /myбеты          — user's active bets
  /balance         — TON wallet balance
  /resolve <id>    — resolve a market you created

Outbound notifications (called from other parts of the backend):
  notify_bet_placed()
  notify_market_resolved()
  notify_payout_ready()
"""
import hashlib
import hmac
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Request, HTTPException, Header

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import User, Market, Bet, MarketStatus
from app.services.market_service import market_service
from app.services.bet_service import bet_service
from sqlalchemy import select

log = logging.getLogger(__name__)
router = APIRouter()

TELEGRAM_API = "https://api.telegram.org"


# ── Signature verification ─────────────────────────────────────────────────────

def verify_telegram_signature(body: bytes, secret_token: str) -> bool:
    """
    Verify the X-Telegram-Bot-Api-Secret-Token header.
    Set this when registering the webhook:
      POST /setWebhook?url=...&secret_token=MY_SECRET
    """
    if not settings.TELEGRAM_WEBHOOK_SECRET:
        return True  # dev mode: skip
    expected = hmac.new(
        settings.TELEGRAM_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, secret_token)


# ── HTTP helper ────────────────────────────────────────────────────────────────

async def tg_post(method: str, payload: dict) -> dict:
    """Call a Telegram Bot API method."""
    if not settings.TELEGRAM_BOT_TOKEN:
        log.warning(f"No TELEGRAM_BOT_TOKEN, skipping {method}")
        return {}
    url = f"{TELEGRAM_API}/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload)
        return resp.json()


async def send_message(
    chat_id: int,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: str = "HTML",
) -> dict:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await tg_post("sendMessage", payload)


def mini_app_button(text: str = "Открыть приложение") -> dict:
    """Inline keyboard with a Web App launch button."""
    return {
        "inline_keyboard": [[
            {
                "text": text,
                "web_app": {"url": settings.MINI_APP_URL},
            }
        ]]
    }


# ── Command handlers ───────────────────────────────────────────────────────────

async def handle_start(chat_id: int, user: dict) -> None:
    name = user.get("first_name", "друг")
    text = (
        f"👋 Привет, <b>{name}</b>!\n\n"
        f"<b>TON Prediction</b> — децентрализованный тотализатор на блокчейне TON.\n\n"
        f"🎯 Делай ставки на любые события\n"
        f"💎 Выплаты автоматически через смарт-контракт\n"
        f"🔒 Без манипуляций — всё на блокчейне\n\n"
        f"Нажми кнопку ниже, чтобы открыть:"
    )
    await send_message(chat_id, text, reply_markup=mini_app_button())


async def handle_markets(chat_id: int) -> None:
    async with AsyncSessionLocal() as db:
        markets, _ = await market_service.list_markets(
            db, status=MarketStatus.OPEN, page=1, page_size=5
        )

    if not markets:
        await send_message(chat_id, "Сейчас нет открытых рынков. Создай первый!")
        return

    lines = ["<b>🔥 Активные рынки:</b>\n"]
    for m in markets:
        total = round(m.total_pool / 1_000_000_000, 2)
        lines.append(
            f"• <b>{m.title[:60]}</b>\n"
            f"  Пул: {total} TON | Да {m.yes_pct}% / Нет {m.no_pct}%"
        )

    await send_message(
        chat_id,
        "\n\n".join(lines),
        reply_markup=mini_app_button("Смотреть все →"),
    )


async def handle_my_bets(chat_id: int, telegram_id: int) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            await send_message(chat_id, "Сначала открой приложение и подключи кошелёк.")
            return

        stats = await bet_service.get_user_stats(db, user.id)

    won_ton = round(stats["total_won_nano"] / 1_000_000_000, 2)
    wagered_ton = round(stats["total_wagered_nano"] / 1_000_000_000, 2)
    net = round((stats["total_won_nano"] - stats["total_wagered_nano"]) / 1_000_000_000, 2)
    sign = "+" if net >= 0 else ""

    text = (
        f"<b>📊 Твоя статистика:</b>\n\n"
        f"Ставок: {stats['total_bets']}\n"
        f"Выиграно: {stats['won_bets']}\n"
        f"Поставлено: {wagered_ton} TON\n"
        f"Выиграно: {won_ton} TON\n"
        f"Итог: <b>{sign}{net} TON</b>"
    )
    await send_message(chat_id, text, reply_markup=mini_app_button())


async def handle_balance(chat_id: int, telegram_id: int) -> None:
    from app.services.ton_service import ton_service
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()

    if not user or not user.ton_address:
        await send_message(
            chat_id,
            "Кошелёк не подключён. Открой приложение и нажми «Подключить».",
            reply_markup=mini_app_button(),
        )
        return

    balance_nano = await ton_service.get_wallet_balance(user.ton_address)
    balance_ton = round(balance_nano / 1_000_000_000, 4)
    short = user.ton_address[:6] + "..." + user.ton_address[-4:]

    await send_message(
        chat_id,
        f"💎 <b>Баланс кошелька</b>\n\n"
        f"Адрес: <code>{short}</code>\n"
        f"Баланс: <b>{balance_ton} TON</b>",
    )


async def handle_unknown(chat_id: int) -> None:
    await send_message(
        chat_id,
        "Неизвестная команда. Доступные: /start, /markets, /myбеты, /balance",
        reply_markup=mini_app_button(),
    )


# ── Webhook endpoint ───────────────────────────────────────────────────────────

@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
):
    body = await request.body()

    if not verify_telegram_signature(body, x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    update: dict = await request.json()
    log.debug(f"TG update: {update.get('update_id')}")

    message = update.get("message") or update.get("edited_message")
    if not message:
        # Handle callback_query (inline button taps) if needed
        cb = update.get("callback_query")
        if cb:
            await tg_post("answerCallbackQuery", {"callback_query_id": cb["id"]})
        return {"ok": True}

    chat_id: int = message["chat"]["id"]
    tg_user: dict = message.get("from", {})
    telegram_id: int = tg_user.get("id", 0)
    text: str = message.get("text", "").strip()

    # Route commands
    if text.startswith("/start"):
        await handle_start(chat_id, tg_user)
    elif text.startswith("/markets"):
        await handle_markets(chat_id)
    elif text.startswith("/myбеты") or text.startswith("/mybets"):
        await handle_my_bets(chat_id, telegram_id)
    elif text.startswith("/balance"):
        await handle_balance(chat_id, telegram_id)
    else:
        await handle_unknown(chat_id)

    return {"ok": True}


@router.post("/set-webhook")
async def set_webhook(request: Request):
    """
    Convenience endpoint to register the webhook with Telegram.
    Call once after deployment:  POST /api/v1/telegram/set-webhook
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=400, detail="TELEGRAM_BOT_TOKEN not configured")

    body = await request.json()
    webhook_url = body.get("url")
    if not webhook_url:
        raise HTTPException(status_code=400, detail="Missing 'url' in body")

    result = await tg_post("setWebhook", {
        "url": webhook_url,
        "allowed_updates": ["message", "callback_query"],
        "secret_token": settings.TELEGRAM_WEBHOOK_SECRET or "",
        "drop_pending_updates": True,
    })
    return result


@router.get("/webhook-info")
async def webhook_info():
    """Check current webhook status."""
    return await tg_post("getWebhookInfo", {})


# ── Outbound notification helpers (called from other modules) ──────────────────

async def notify_bet_placed(telegram_id: int, market_title: str, outcome: str, amount_ton: float) -> None:
    """DM the bettor right after their bet is accepted."""
    outcome_str = "ДА ✅" if outcome == "yes" else "НЕТ ❌"
    await send_message(
        telegram_id,
        f"🎯 <b>Ставка принята!</b>\n\n"
        f"Рынок: {market_title[:80]}\n"
        f"Исход: {outcome_str}\n"
        f"Сумма: <b>{amount_ton} TON</b>\n\n"
        f"Следи за результатом в приложении.",
        reply_markup=mini_app_button(),
    )


async def notify_market_resolved(
    telegram_id: int,
    market_title: str,
    winning_outcome: str,
    is_winner: bool,
    payout_ton: float = 0.0,
) -> None:
    """Notify a user when a market they bet on is resolved."""
    if is_winner:
        text = (
            f"🏆 <b>Ты выиграл!</b>\n\n"
            f"Рынок: {market_title[:80]}\n"
            f"Победный исход: {'ДА' if winning_outcome == 'yes' else 'НЕТ'}\n"
            f"Выплата: <b>{payout_ton} TON</b>\n\n"
            f"Нажми «Забрать выигрыш» в приложении."
        )
    else:
        text = (
            f"😔 <b>Увы, не повезло</b>\n\n"
            f"Рынок: {market_title[:80]}\n"
            f"Победный исход: {'ДА' if winning_outcome == 'yes' else 'НЕТ'}\n\n"
            f"Попробуй удачу в следующий раз!"
        )
    await send_message(telegram_id, text, reply_markup=mini_app_button())


async def notify_payout_ready(telegram_id: int, market_title: str, payout_ton: float) -> None:
    await send_message(
        telegram_id,
        f"💎 <b>Выплата готова!</b>\n\n"
        f"{market_title[:80]}\n"
        f"Сумма: <b>{payout_ton} TON</b>\n\n"
        f"Открой приложение, чтобы получить TON на кошелёк.",
        reply_markup=mini_app_button(),
    )
