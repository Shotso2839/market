# TON Prediction Market — Backend

FastAPI + PostgreSQL + TON blockchain backend for the Telegram Mini App prediction market.

---

## Stack

| Layer | Technology |
|---|---|
| API framework | FastAPI 0.115 |
| Database | PostgreSQL 16 + SQLAlchemy 2 (async) |
| Migrations | Alembic |
| TON integration | toncenter.com HTTP API v2 |
| Auth | Telegram initData HMAC verification |
| Wallet connect | TON Connect v2 proof verification |
| Containerisation | Docker + Docker Compose |

---

## Project structure

```
backend/
├── app/
│   ├── main.py              # FastAPI app, CORS, lifespan
│   ├── config.py            # Settings from .env
│   ├── database.py          # Async SQLAlchemy engine + session
│   ├── models.py            # ORM models: User, Market, Bet
│   ├── schemas.py           # Pydantic request/response schemas
│   ├── dependencies.py      # Auth dependency (get_current_user)
│   ├── tasks.py             # Background jobs (close expired markets, chain sync)
│   ├── routers/
│   │   ├── markets.py       # CRUD + resolve endpoints
│   │   ├── bets.py          # Place bet, history, stats
│   │   ├── users.py         # Profile, wallet link, balance
│   │   └── ton.py           # TON Connect, tx verify, contract state
│   └── services/
│       ├── market_service.py
│       ├── bet_service.py
│       └── ton_service.py   # TON HTTP API + Telegram auth
├── contracts/
│   └── prediction_market.fc # FunC smart contract source
├── alembic/
│   └── env.py               # Async Alembic runner
├── alembic.ini
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## Quick start

### 1. Clone and configure

```bash
cp .env.example .env
# Edit .env: add TON_API_KEY and TELEGRAM_BOT_TOKEN
```

### 2. Run with Docker Compose

```bash
docker compose up --build
```

API available at `http://localhost:8000`  
Interactive docs at `http://localhost:8000/docs`

### 3. Run migrations

```bash
# Inside the container:
docker compose exec api alembic upgrade head

# Or locally:
pip install -r requirements.txt
alembic upgrade head
```

---

## API endpoints

### Auth
All protected endpoints require the header:
```
X-Init-Data: <Telegram WebApp initData string>
```

### Markets

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/markets` | List markets (filterable by status, category) |
| POST | `/api/v1/markets` | Create new market |
| GET | `/api/v1/markets/{id}` | Get single market |
| POST | `/api/v1/markets/{id}/resolve` | Declare winning outcome |
| PATCH | `/api/v1/markets/{id}/contract` | Link deployed contract address |

### Bets

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/bets` | Place a bet (with optional tx_hash verification) |
| GET | `/api/v1/bets/my` | Current user's bet history |
| GET | `/api/v1/bets/my/stats` | Aggregated win/loss stats |
| GET | `/api/v1/bets/market/{id}` | All bets for a market |
| POST | `/api/v1/bets/{id}/confirm` | Confirm a pending bet tx |

### Users

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/users/me` | Get profile |
| PATCH | `/api/v1/users/me` | Update username / link wallet |
| GET | `/api/v1/users/me/balance` | Live TON balance from chain |

### TON Blockchain

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/ton/connect` | Verify TON Connect proof, link wallet |
| POST | `/api/v1/ton/verify-tx` | Verify a transaction on chain |
| GET | `/api/v1/ton/contract/{address}/state` | Read contract state |
| GET | `/api/v1/ton/balance/{address}` | Get any address balance |

---

## TON integration flow

```
User wallet ──[TON Connect]──► POST /ton/connect  (proof verification)
                                      │
                                      ▼
User places bet ──[sends TON to contract]──► POST /bets  (with tx_hash)
                                                   │
                                          verify_transaction()
                                          checks toncenter API
                                                   │
                                                   ▼
                                          Bet recorded in DB

Event ends ──► Creator calls POST /markets/{id}/resolve
                       │
                       ▼
              Payouts calculated in DB
              (Frontend reads payout_ton from GET /bets/my)
                       │
                       ▼
              User calls contract op 0x04 (claim_payout) directly
              Contract sends TON to winner's wallet
```

---

## Smart contract (FunC)

`contracts/prediction_market.fc`

One contract is deployed per market. Op codes:

| Op | Name | Who |
|---|---|---|
| `0x01` | `place_bet(outcome)` | Any user |
| `0x02` | `resolve(outcome)` | Creator only |
| `0x03` | `cancel` | Creator only |
| `0x04` | `claim_payout` | Winners / refunds |

To compile and deploy:
```bash
# Install toncli or use Blueprint (https://github.com/ton-org/blueprint)
npm create ton@latest
# or
pip install toncli
toncli deploy contracts/prediction_market.fc --network testnet
```

After deployment, call:
```
PATCH /api/v1/markets/{id}/contract
  ?contract_address=<deployed_address>
  &deploy_tx_hash=<tx_hash>
```

---

## Background tasks

```bash
# Run alongside the API (separate process):
python -m app.tasks
```

Tasks:
- **Every 60s**: close expired markets (bet_closes_at passed)
- **Every 5min**: sync on-chain pool sizes and status into DB

---

## Environment variables

See `.env.example` for all variables. Key ones:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/tonpred
TON_API_KEY=           # from toncenter.com (free tier: 1 req/s)
TON_NETWORK=testnet    # or mainnet for production
TELEGRAM_BOT_TOKEN=    # from @BotFather
PLATFORM_FEE_BPS=200   # 2% platform fee
```

---

## Production checklist

- [ ] Set `APP_ENV=production` and a strong `SECRET_KEY`
- [ ] Switch `TON_NETWORK=mainnet` and update `TON_API_URL`
- [ ] Enable full Ed25519 signature check in `ton_service.verify_ton_connect_proof()`
- [ ] Deploy the FunC contract and set `PREDICTION_CONTRACT_ADDRESS`
- [ ] Add Redis + run background tasks with APScheduler or Celery Beat
- [ ] Set up HTTPS (Nginx + Certbot)
- [ ] Configure `ALLOWED_ORIGINS` to your Mini App domain
