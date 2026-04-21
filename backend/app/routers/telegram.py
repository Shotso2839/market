"""
Telegram bot integration for TONPRED.

Supports:
- webhook delivery
- long polling for local development
- bot command configuration
- Mini App / URL fallback button rendering
"""

import logging
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import MarketStatus, User
from app.services.bet_service import bet_service
from app.services.market_service import market_service

log = logging.getLogger(__name__)
router = APIRouter()

TELEGRAM_API = "https://api.telegram.org"


def verify_telegram_signature(body: bytes, secret_token: str) -> bool:
    """
    Telegram sends the configured secret token as-is in the header.
    """
    del body  # not used; kept for a stable function signature
    if not settings.TELEGRAM_WEBHOOK_SECRET:
        return True
    return secret_token == settings.TELEGRAM_WEBHOOK_SECRET


async def tg_post(method: str, payload: dict, timeout: float = 10.0) -> dict:
    """Call a Telegram Bot API method and return the JSON payload."""
    if not settings.TELEGRAM_BOT_TOKEN:
        log.warning("No TELEGRAM_BOT_TOKEN, skipping %s", method)
        return {}

    url = f"{TELEGRAM_API}/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            if not data.get("ok", True):
                log.warning("Telegram %s failed: %s", method, data.get("description"))
            return data
    except httpx.HTTPError as exc:
        log.warning("Telegram %s request failed: %s", method, exc)
        return {"ok": False, "description": str(exc)}


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


def _supports_web_app_button(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False

    local_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
    return host not in local_hosts and not host.endswith(".local")


def mini_app_button(text: str = "Open TONPRED") -> Optional[dict]:
    """
    Use a Web App button only for public HTTPS URLs.
    For local development, fall back to a normal URL button.
    """
    url = (settings.MINI_APP_URL or "").strip()
    if not url:
        return None

    if _supports_web_app_button(url):
        button = {
            "text": text,
            "web_app": {"url": url},
        }
    else:
        button = {
            "text": text,
            "url": url,
        }

    return {"inline_keyboard": [[button]]}


def _launch_note() -> str:
    url = (settings.MINI_APP_URL or "").strip()
    if not url:
        return "Mini App URL is not configured yet."
    if _supports_web_app_button(url):
        return "Tap the button below to open TONPRED inside Telegram."
    return (
        "Tap the button below to open the app in an external browser. "
        "Telegram Mini Apps require a public HTTPS URL."
    )


async def configure_bot() -> None:
    """Set bot commands and the chat menu button."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return

    await tg_post(
        "setMyCommands",
        {
            "commands": [
                {"command": "start", "description": "Open TONPRED"},
                {"command": "markets", "description": "Show active markets"},
                {"command": "mybets", "description": "Show my betting stats"},
                {"command": "balance", "description": "Show wallet balance"},
            ]
        },
    )

    mini_app_url = (settings.MINI_APP_URL or "").strip()
    if _supports_web_app_button(mini_app_url):
        await tg_post(
            "setChatMenuButton",
            {
                "menu_button": {
                    "type": "web_app",
                    "text": "Open app",
                    "web_app": {"url": mini_app_url},
                }
            },
        )
        return

    await tg_post(
        "setChatMenuButton",
        {
            "menu_button": {
                "type": "commands",
            }
        },
    )


def _normalize_command(text: str) -> str:
    command = (text or "").strip().split(maxsplit=1)[0].lower()
    return command.split("@", maxsplit=1)[0]


async def handle_start(chat_id: int, user: dict) -> None:
    name = user.get("first_name") or "friend"
    text = (
        f"Hello, <b>{name}</b>!\n\n"
        f"<b>TONPRED</b> is a prediction market on TON.\n\n"
        f"- Place bets on market outcomes\n"
        f"- Track open and resolved markets\n"
        f"- Connect your TON wallet and manage positions\n\n"
        f"{_launch_note()}"
    )
    await send_message(chat_id, text, reply_markup=mini_app_button())


async def handle_markets(chat_id: int) -> None:
    async with AsyncSessionLocal() as db:
        markets, _ = await market_service.list_markets(
            db,
            status=MarketStatus.OPEN,
            page=1,
            page_size=5,
        )

    if not markets:
        await send_message(chat_id, "There are no open markets right now.")
        return

    lines = ["<b>Active markets</b>"]
    for market in markets:
        total = round(market.total_pool / 1_000_000_000, 2)
        lines.append(
            f"\n<b>{market.title[:60]}</b>\n"
            f"Pool: {total} TON | YES {market.yes_pct}% / NO {market.no_pct}%"
        )

    await send_message(
        chat_id,
        "\n".join(lines),
        reply_markup=mini_app_button("Open markets"),
    )


async def handle_my_bets(chat_id: int, telegram_id: int) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            await send_message(
                chat_id,
                "Open the app first so we can link your Telegram profile to your wallet.",
                reply_markup=mini_app_button(),
            )
            return

        stats = await bet_service.get_user_stats(db, user.id)

    won_ton = round(stats["total_won_nano"] / 1_000_000_000, 2)
    wagered_ton = round(stats["total_wagered_nano"] / 1_000_000_000, 2)
    net_ton = round(
        (stats["total_won_nano"] - stats["total_wagered_nano"]) / 1_000_000_000,
        2,
    )
    sign = "+" if net_ton >= 0 else ""

    text = (
        "<b>Your stats</b>\n\n"
        f"Bets: {stats['total_bets']}\n"
        f"Wins: {stats['won_bets']}\n"
        f"Wagered: {wagered_ton} TON\n"
        f"Won: {won_ton} TON\n"
        f"Net: <b>{sign}{net_ton} TON</b>"
    )
    await send_message(chat_id, text, reply_markup=mini_app_button())


async def handle_balance(chat_id: int, telegram_id: int) -> None:
    from app.services.ton_service import ton_service

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()

    if not user or not user.ton_address:
        await send_message(
            chat_id,
            "Wallet is not connected yet. Open the app and connect your TON wallet first.",
            reply_markup=mini_app_button(),
        )
        return

    balance_nano = await ton_service.get_wallet_balance(user.ton_address)
    balance_ton = round(balance_nano / 1_000_000_000, 4)
    short = user.ton_address[:6] + "..." + user.ton_address[-4:]
    await send_message(
        chat_id,
        f"<b>Wallet balance</b>\n\nAddress: <code>{short}</code>\nBalance: <b>{balance_ton} TON</b>",
        reply_markup=mini_app_button(),
    )


async def handle_unknown(chat_id: int) -> None:
    await send_message(
        chat_id,
        "Unknown command. Available commands: /start, /markets, /mybets, /balance",
        reply_markup=mini_app_button(),
    )


async def process_update(update: dict) -> None:
    """Process one Telegram update for both webhook and long-polling modes."""
    log.debug("TG update: %s", update.get("update_id"))

    message = update.get("message") or update.get("edited_message")
    if not message:
        callback = update.get("callback_query")
        if callback:
            await tg_post(
                "answerCallbackQuery",
                {"callback_query_id": callback["id"]},
            )
        return

    chat_id = int(message["chat"]["id"])
    tg_user = message.get("from", {})
    telegram_id = int(tg_user.get("id", 0))
    command = _normalize_command(message.get("text", ""))

    if command == "/start":
        await handle_start(chat_id, tg_user)
    elif command == "/markets":
        await handle_markets(chat_id)
    elif command in {"/mybets", "/myР±РµС‚С‹"}:
        await handle_my_bets(chat_id, telegram_id)
    elif command == "/balance":
        await handle_balance(chat_id, telegram_id)
    else:
        await handle_unknown(chat_id)


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
):
    body = await request.body()
    if not verify_telegram_signature(body, x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    update = await request.json()
    await process_update(update)
    return {"ok": True}


@router.post("/set-webhook")
async def set_webhook(request: Request):
    if not settings.TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=400, detail="TELEGRAM_BOT_TOKEN not configured")

    body = await request.json()
    webhook_url = body.get("url")
    if not webhook_url:
        raise HTTPException(status_code=400, detail="Missing 'url' in body")

    return await tg_post(
        "setWebhook",
        {
            "url": webhook_url,
            "allowed_updates": ["message", "callback_query"],
            "secret_token": settings.TELEGRAM_WEBHOOK_SECRET or "",
            "drop_pending_updates": bool(body.get("drop_pending_updates", True)),
        },
    )


@router.post("/delete-webhook")
async def delete_webhook(request: Request):
    body = {}
    if request.headers.get("content-length") not in {None, "", "0"}:
        try:
            body = await request.json()
        except Exception:
            body = {}

    return await tg_post(
        "deleteWebhook",
        {"drop_pending_updates": bool(body.get("drop_pending_updates", False))},
    )


@router.get("/webhook-info")
async def webhook_info():
    return await tg_post("getWebhookInfo", {})


@router.post("/configure-bot")
async def configure_bot_endpoint():
    await configure_bot()
    return {"ok": True}


async def notify_bet_placed(
    telegram_id: int,
    market_title: str,
    outcome: str,
    amount_ton: float,
) -> None:
    outcome_label = "YES" if outcome == "yes" else "NO"
    await send_message(
        telegram_id,
        (
            "<b>Bet accepted</b>\n\n"
            f"Market: {market_title[:80]}\n"
            f"Outcome: {outcome_label}\n"
            f"Amount: <b>{amount_ton} TON</b>"
        ),
        reply_markup=mini_app_button(),
    )


async def notify_market_resolved(
    telegram_id: int,
    market_title: str,
    winning_outcome: str,
    is_winner: bool,
    payout_ton: float = 0.0,
) -> None:
    winning_label = "YES" if winning_outcome == "yes" else "NO"
    if is_winner:
        text = (
            "<b>You won</b>\n\n"
            f"Market: {market_title[:80]}\n"
            f"Winning outcome: {winning_label}\n"
            f"Payout: <b>{payout_ton} TON</b>"
        )
    else:
        text = (
            "<b>Market resolved</b>\n\n"
            f"Market: {market_title[:80]}\n"
            f"Winning outcome: {winning_label}"
        )

    await send_message(telegram_id, text, reply_markup=mini_app_button())


async def notify_payout_ready(
    telegram_id: int,
    market_title: str,
    payout_ton: float,
) -> None:
    await send_message(
        telegram_id,
        (
            "<b>Payout ready</b>\n\n"
            f"{market_title[:80]}\n"
            f"Amount: <b>{payout_ton} TON</b>"
        ),
        reply_markup=mini_app_button(),
    )
