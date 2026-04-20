/**
 * TON Prediction — Frontend API & WebSocket client
 * Drop this file next to index.html and add:
 *   <script src="api.js"></script>
 * before your app script tag.
 */

// ── Config ────────────────────────────────────────────────────────────────────

const API_BASE = window.__TONPRED_API_BASE__
  || (window.location.hostname === 'localhost'
    ? 'http://localhost:8000/api/v1'
    : '/api/v1');                        // same-origin in production

const WS_BASE = API_BASE
  .replace('http://', 'ws://')
  .replace('https://', 'wss://');

// ── Auth header ───────────────────────────────────────────────────────────────

function getInitData() {
  return window.Telegram?.WebApp?.initData || 'dev_mode';
}

function authHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-Init-Data': getInitData(),
  };
}

const CATEGORY_TO_BACKEND = {
  Sports: '\u0421\u043f\u043e\u0440\u0442',
  Crypto: '\u041a\u0440\u0438\u043f\u0442\u043e',
  Politics: '\u041f\u043e\u043b\u0438\u0442\u0438\u043a\u0430',
  Weather: '\u041f\u043e\u0433\u043e\u0434\u0430',
  Other: '\u0414\u0440\u0443\u0433\u043e\u0435',
};

const CATEGORY_FROM_BACKEND = {
  '\u0421\u043f\u043e\u0440\u0442': 'Sports',
  '\u041a\u0440\u0438\u043f\u0442\u043e': 'Crypto',
  '\u041f\u043e\u043b\u0438\u0442\u0438\u043a\u0430': 'Politics',
  '\u041f\u043e\u0433\u043e\u0434\u0430': 'Weather',
  '\u0414\u0440\u0443\u0433\u043e\u0435': 'Other',
};

function toBackendCategory(category) {
  return CATEGORY_TO_BACKEND[category] || category;
}

function fromBackendCategory(category) {
  return CATEGORY_FROM_BACKEND[category] || category;
}

function normalizeMarket(market) {
  return market ? { ...market, category: fromBackendCategory(market.category) } : market;
}

function normalizeMarketPage(page) {
  return page?.items ? { ...page, items: page.items.map(normalizeMarket) } : page;
}

// ── Generic fetch wrapper ─────────────────────────────────────────────────────

async function apiFetch(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: authHeaders(),
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ── Markets ───────────────────────────────────────────────────────────────────

const Markets = {
  list({ status, category, page = 1, pageSize = 20 } = {}) {
    const params = new URLSearchParams({ page, page_size: pageSize });
    if (status)   params.set('status', status);
    if (category) params.set('category', toBackendCategory(category));
    return apiFetch(`/markets?${params}`).then(normalizeMarketPage);
  },

  get(id) {
    return apiFetch(`/markets/${id}`).then(normalizeMarket);
  },

  create({ title, description, category, oracleType, betClosesAt }) {
    return apiFetch('/markets', {
      method: 'POST',
      body: JSON.stringify({
        title,
        description,
        category: toBackendCategory(category),
        oracle_type: oracleType,
        bet_closes_at: betClosesAt,
      }),
    }).then(normalizeMarket);
  },

  resolve(id, winningOutcome, resolutionTxHash = null) {
    return apiFetch(`/markets/${id}/resolve`, {
      method: 'POST',
      body: JSON.stringify({ winning_outcome: winningOutcome, resolution_tx_hash: resolutionTxHash }),
    }).then(normalizeMarket);
  },
};

// ── Bets ──────────────────────────────────────────────────────────────────────

const Bets = {
  place({ marketId, outcome, amountTon, txHash = null }) {
    return apiFetch('/bets', {
      method: 'POST',
      body: JSON.stringify({
        market_id: marketId,
        outcome,
        amount_ton: amountTon,
        tx_hash: txHash,
      }),
    });
  },

  myBets(marketId = null) {
    const params = marketId ? `?market_id=${marketId}` : '';
    return apiFetch(`/bets/my${params}`);
  },

  myStats() {
    return apiFetch('/bets/my/stats');
  },

  marketBets(marketId) {
    return apiFetch(`/bets/market/${marketId}`);
  },

  confirm(betId, txHash) {
    return apiFetch(`/bets/${betId}/confirm?tx_hash=${txHash}`, { method: 'POST' });
  },
};

// ── Users ─────────────────────────────────────────────────────────────────────

const Users = {
  me() {
    return apiFetch('/users/me');
  },

  update({ username, tonAddress } = {}) {
    return apiFetch('/users/me', {
      method: 'PATCH',
      body: JSON.stringify({ username, ton_address: tonAddress }),
    });
  },

  balance() {
    return apiFetch('/users/me/balance');
  },
};

// ── TON Connect ───────────────────────────────────────────────────────────────

const TonApi = {
  connect({ address, proof, network = 'testnet' }) {
    return apiFetch('/ton/connect', {
      method: 'POST',
      body: JSON.stringify({ address, proof, network }),
    });
  },

  verifyTx({ txHash, expectedDestination, expectedAmountNano }) {
    return apiFetch('/ton/verify-tx', {
      method: 'POST',
      body: JSON.stringify({
        tx_hash: txHash,
        expected_destination: expectedDestination,
        expected_amount_nano: expectedAmountNano,
      }),
    });
  },

  contractState(address) {
    return apiFetch(`/ton/contract/${address}/state`);
  },
};

window.TonApi = TonApi;

// ── WebSocket manager ─────────────────────────────────────────────────────────

class MarketSocket {
  /**
   * Live updates for a single market.
   *
   * Usage:
   *   const sock = new MarketSocket('market-uuid-here');
   *   sock.on('market_update', ({ yes_pct, no_pct, total_pool_ton }) => { ... });
   *   sock.on('new_bet',       ({ outcome, amount_ton }) => { ... });
   *   sock.on('market_resolved', ({ winning_outcome }) => { ... });
   *   sock.connect();
   *   // later:
   *   sock.disconnect();
   */
  constructor(marketId) {
    this.marketId = marketId;
    this._handlers = {};
    this._ws = null;
    this._reconnectDelay = 2000;
    this._shouldReconnect = true;
  }

  on(event, handler) {
    this._handlers[event] = handler;
    return this;
  }

  connect() {
    this._shouldReconnect = true;
    this._open();
    return this;
  }

  disconnect() {
    this._shouldReconnect = false;
    this._ws?.close();
    this._ws = null;
  }

  _open() {
    const url = `${WS_BASE}/ws/market/${this.marketId}?init_data=${encodeURIComponent(getInitData())}`;
    this._ws = new WebSocket(url);

    this._ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        const handler = this._handlers[msg.event];
        if (handler) handler(msg);
      } catch {}
    };

    this._ws.onclose = () => {
      if (this._shouldReconnect) {
        setTimeout(() => this._open(), this._reconnectDelay);
        this._reconnectDelay = Math.min(this._reconnectDelay * 1.5, 30000);
      }
    };

    this._ws.onopen = () => {
      this._reconnectDelay = 2000; // reset backoff
    };
  }

  ping() {
    this._ws?.readyState === WebSocket.OPEN &&
      this._ws.send(JSON.stringify({ action: 'ping' }));
  }
}

class UserSocket {
  /**
   * Personal notifications channel (requires connected wallet / initData).
   *
   * Usage:
   *   const sock = new UserSocket();
   *   sock.on('bet_confirmed',   ({ bet_id, tx_hash }) => { ... });
   *   sock.on('payout_ready',    ({ payout_ton }) => { ... });
   *   sock.on('market_resolved', ({ market_id, winning_outcome }) => { ... });
   *   sock.connect();
   */
  constructor() {
    this._handlers = {};
    this._ws = null;
    this._shouldReconnect = true;
    this._reconnectDelay = 2000;
  }

  on(event, handler) {
    this._handlers[event] = handler;
    return this;
  }

  connect() {
    this._shouldReconnect = true;
    this._open();
    return this;
  }

  disconnect() {
    this._shouldReconnect = false;
    this._ws?.close();
  }

  _open() {
    const url = `${WS_BASE}/ws/user?init_data=${encodeURIComponent(getInitData())}`;
    this._ws = new WebSocket(url);

    this._ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        const handler = this._handlers[msg.event];
        if (handler) handler(msg);
      } catch {}
    };

    this._ws.onclose = () => {
      if (this._shouldReconnect) {
        setTimeout(() => this._open(), this._reconnectDelay);
        this._reconnectDelay = Math.min(this._reconnectDelay * 1.5, 30000);
      }
    };

    this._ws.onopen = () => { this._reconnectDelay = 2000; };
  }
}

// ── Telegram WebApp helpers ───────────────────────────────────────────────────

const TelegramApp = {
  /** Expand to full screen, hide loading splash */
  init() {
    if (!window.Telegram?.WebApp) return;
    const twa = window.Telegram.WebApp;
    twa.expand();
    twa.ready();
    twa.enableClosingConfirmation();
    return twa;
  },

  /** Show native Telegram back button */
  showBackButton(onClick) {
    window.Telegram?.WebApp?.BackButton?.show();
    window.Telegram?.WebApp?.BackButton?.onClick(onClick);
  },

  hideBackButton() {
    window.Telegram?.WebApp?.BackButton?.hide();
  },

  /** Haptic feedback */
  haptic(type = 'light') {
    window.Telegram?.WebApp?.HapticFeedback?.impactOccurred(type);
  },

  /** Open a URL inside Telegram browser */
  openLink(url) {
    window.Telegram?.WebApp?.openLink(url);
  },
};

// ── Example: wire everything into your existing app ───────────────────────────
//
// (1) On app start — init Telegram + load markets from API:
//
//   TelegramApp.init();
//
//   Markets.list({ status: 'open' }).then(data => {
//     // data.items replaces the `markets` array in your app
//     renderMarkets(data.items);
//   }).catch(() => {
//     // API unreachable — keep mock data
//   });
//
// (2) When user opens a market card — subscribe to live updates:
//
//   const sock = new MarketSocket(market.id)
//     .on('market_update', (msg) => {
//       updateBars(msg.yes_pct, msg.no_pct, msg.total_pool_ton);
//     })
//     .on('new_bet', (msg) => {
//       showToast(`Новая ставка: ${msg.amount_ton} TON на ${msg.outcome === 'yes' ? 'ДА' : 'НЕТ'}`);
//       updateBars(msg.yes_pct, msg.no_pct);
//     })
//     .on('market_resolved', (msg) => {
//       showResolution(msg.winning_outcome);
//     })
//     .connect();
//
// (3) Personal channel for the whole session:
//
//   const userSock = new UserSocket()
//     .on('bet_confirmed', (msg) => {
//       showToast('Ставка подтверждена в блокчейне ✓');
//     })
//     .on('payout_ready', (msg) => {
//       showToast(`Выигрыш готов: ${msg.payout_ton} TON!`);
//     })
//     .connect();
//
// (4) When user confirms a bet:
//
//   async function confirmBet(marketId, outcome, amountTon) {
//     try {
//       const bet = await Bets.place({ marketId, outcome, amountTon });
//       showToast('Ставка принята!');
//       return bet;
//     } catch (e) {
//       showToast(e.message, true);
//     }
//   }

// Export for module usage (optional)
if (typeof module !== 'undefined') {
  module.exports = { Markets, Bets, Users, TonApi, MarketSocket, UserSocket, TelegramApp };
}
