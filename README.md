# TON Pred — Prediction Market on TON

Первая CLOB/LMSR prediction market нативно в Telegram на блокчейне TON.

Купи/продай позицию в любой момент. Динамическая комиссия θ×p×(1-p) как у Polymarket. Без KYC. Без MetaMask.

---

## Структура проекта

```
tonpred2/
├── backend/          FastAPI + PostgreSQL + CLOB движок + оракулы
├── contract/         FunC смарт-контракт (Blueprint/TypeScript)
├── frontend/         Telegram Mini App (vanilla JS)
└── nginx/            Nginx конфиг + Docker Compose для прода
```

---

## Быстрый старт

### 1. Backend

```bash
cd backend
cp .env.example .env          # заполни переменные
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

### 2. Frontend

Открой `frontend/index.html` в браузере — или задеплой на любой статик-хостинг.

Обнови `frontend/tonconnect-manifest.json` — замени `url` и `iconUrl` на свой домен.

### 3. Контракт

```bash
cd contract
npm install
npx blueprint build
npx blueprint run deployPredictionMarket --testnet
```

---

## Переменные окружения (backend/.env)

| Переменная | Описание |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `TON_API_KEY` | API ключ toncenter.com |
| `TELEGRAM_BOT_TOKEN` | Токен бота от @BotFather |
| `PLATFORM_TON_ADDRESS` | TON-адрес платформы для комиссий |
| `DEPLOYER_MNEMONIC` | 24 слова для деплоя контракта |
| `SECRET_KEY` | JWT secret для подписей |

---

## Ключевые модули

### `backend/app/clob.py` — LMSR движок
CLOB/LMSR маркет-мейкер. 23/23 тестов.

```python
from app.clob import CLOBMarket, Outcome, buy_shares, sell_shares, price_impact

m = CLOBMarket("btc-100k", b=200.0)
shares, cost = buy_shares(m, Outcome.YES, 10.0)   # купить YES за 10 TON
proceeds = sell_shares(m, Outcome.YES, shares)     # продать в любой момент
impact = price_impact(m, Outcome.YES, 50.0)        # preview перед покупкой
```

### `backend/app/fees.py` — Динамическая комиссия
Polymarket-style: `fee = amount × θ × p × (1-p)`

Максимум 1.5% при p=50%, почти 0% при очевидном исходе. 24/24 тестов.

```python
from app.fees import dynamic_taker_fee, MarketCategory

result = dynamic_taker_fee(
    amount_nano=10_000_000_000,   # 10 TON
    yes_probability=0.50,          # 50/50 → пик комиссии
    category=MarketCategory.CRYPTO,
    user_bet_count=1
)
# result.effective_rate_pct ≈ 1.5%
```

### `backend/app/oracles/` — Авто-оракулы
- `crypto.py` — Pyth Network (BTC/ETH/TON/SOL, без API-ключа)
- `sports.py` — football-data.org
- `weather.py` — Open-Meteo (без API-ключа)
- `dispatcher.py` — авто-разрешение при confidence ≥ 0.8

### `contract/contracts/PredictionMarket.fc` — Смарт-контракт
FunC контракт паримьютельного рынка.

| Op | Действие |
|---|---|
| `0x01` | place_bet — поставить на YES или NO |
| `0x02` | resolve — разрешить рынок (только создатель) |
| `0x03` | cancel — отменить рынок |
| `0x04` | claim_payout — забрать выигрыш |

> ⚠️ CLOB sell_shares (op 0x05) — в разработке. Текущий контракт — паримьютель. LMSR-ценообразование реализовано off-chain в `clob.py`.

---

## Тесты

```bash
cd backend

# CLOB движок (23/23)
python tests/test_clob.py

# Динамическая fee (24/24)
python tests/test_fees.py

# Оракулы (41/41)
python tests/test_oracles.py

# Юнит-тесты (53/53)
python tests/test_unit.py
```

---

## Деплой на прод

```bash
cd nginx
cp docker-compose.prod.yml ../docker-compose.prod.yml
# Заполни переменные в .env
docker-compose -f docker-compose.prod.yml up -d
```

---

## Архитектура

```
Telegram Mini App (index.html)
        │  TON Connect
        ▼
FastAPI Backend (app/main.py)
  ├── CLOB API (/buy, /sell, /price)
  ├── Markets API (/markets)
  ├── Bets API (/bets)
  ├── WebSocket (/ws)
  └── Oracles (auto-resolve)
        │
        ▼
PostgreSQL ──► TON Blockchain
                (PredictionMarket.fc)
```

---

## Лицензия

MIT
