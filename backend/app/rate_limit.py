"""
rate_limit.py
Rate limiting через slowapi (wrapper вокруг limits).

Установка: pip install slowapi

Зоны:
  default  — 60 req/min на IP   (все публичные эндпоинты)
  bets     — 10 req/min на user (POST /bets)
  markets  — 20 req/min на user (POST /markets)
  auth     — 5  req/min на IP   (TON Connect, wallet ops)
"""

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request, Response
from fastapi.responses import JSONResponse


# ── Key functions ─────────────────────────────────────────────────────────────

def get_user_key(request: Request) -> str:
    """Rate limit by Telegram user ID when available, else by IP."""
    # X-Init-Data header содержит telegram_id после верификации
    # Для простоты используем IP — в продакшне заменить на user.id
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.client.host if request.client else '127.0.0.1'


def get_ip_key(request: Request) -> str:
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.client.host if request.client else '127.0.0.1'


# ── Limiter instance ──────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_user_key, default_limits=['60/minute'])


# ── Custom 429 handler ────────────────────────────────────────────────────────

async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> Response:
    return JSONResponse(
        status_code=429,
        content={
            'detail': 'Слишком много запросов. Подождите немного.',
            'retry_after': str(exc.retry_after) if hasattr(exc, 'retry_after') else '60',
        },
        headers={'Retry-After': str(getattr(exc, 'retry_after', 60))},
    )


# ── Convenience decorators ────────────────────────────────────────────────────

# Используй как:
#   @router.post("/bets")
#   @limiter.limit("10/minute")
#   async def place_bet(request: Request, ...):

LIMIT_BETS    = '10/minute'
LIMIT_MARKETS = '20/minute'
LIMIT_AUTH    = '5/minute'
LIMIT_DEFAULT = '60/minute'
LIMIT_READ    = '120/minute'
