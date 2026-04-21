"""
Telegram long-polling worker for local and development environments.
"""

import asyncio
import logging

from app.config import settings
from app.routers.telegram import configure_bot, process_update, tg_post

log = logging.getLogger(__name__)


async def run_polling() -> None:
    if not settings.TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN is not configured; polling worker will exit")
        return

    await tg_post("deleteWebhook", {"drop_pending_updates": False})
    await configure_bot()

    offset = None
    backoff_seconds = 2

    while True:
        try:
            payload = {
                "timeout": 50,
                "allowed_updates": ["message", "callback_query"],
            }
            if offset is not None:
                payload["offset"] = offset

            response = await tg_post("getUpdates", payload, timeout=60.0)
            if not response.get("ok"):
                raise RuntimeError(response.get("description", "getUpdates failed"))

            for update in response.get("result", []):
                await process_update(update)
                offset = int(update["update_id"]) + 1

            backoff_seconds = 2
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Telegram polling error: %s", exc)
            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, 30)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_polling())


if __name__ == "__main__":
    main()
