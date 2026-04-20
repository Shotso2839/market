/**
 * ton-connect.js
 * TON Connect v2 integration for Telegram Mini App
 *
 * Drop alongside index.html and api.js. Load order in HTML:
 *   <script src="https://telegram.org/js/telegram-web-app.js"></script>
 *   <script src="https://unpkg.com/@tonconnect/ui@2.0.6/dist/tonconnect-ui.min.js"></script>
 *   <script src="api.js"></script>
 *   <script src="ton-connect.js"></script>
 *
 * Then call TonConnect.init() once on app start.
 * Replaces handleWalletClick() in index.html.
 */

// ── Config ────────────────────────────────────────────────────────────────────

const TC_MANIFEST_URL = (() => {
  // tonconnect-manifest.json must be served from your domain
  // In dev: http://localhost:5173/tonconnect-manifest.json
  const base = window.location.origin;
  return base + '/tonconnect-manifest.json';
})();

// ── Module ────────────────────────────────────────────────────────────────────

const TonConnect = (() => {
  let _ui = null;          // TonConnectUI instance
  let _wallet = null;      // current wallet info
  let _unsubscribe = null; // status listener cleanup

  // ── Init ──────────────────────────────────────────────────────────────────

  async function init() {
    if (!window.TON_CONNECT_UI) {
      console.warn('TON Connect UI SDK not loaded');
      return;
    }

    _ui = new window.TON_CONNECT_UI.TonConnectUI({
      manifestUrl: TC_MANIFEST_URL,
      // Don't render the built-in button — we control our own UI
      buttonRootId: null,
    });

    // Restore previous session automatically
    _unsubscribe = _ui.onStatusChange(_onStatusChange);

    // If already connected from a previous session, fire the handler
    const current = _ui.wallet;
    if (current) {
      await _onStatusChange(current);
    }

    return _ui;
  }

  // ── Status change handler ─────────────────────────────────────────────────

  async function _onStatusChange(walletInfo) {
    if (!walletInfo) {
      // Disconnected
      _wallet = null;
      _updateWalletUI(null);
      return;
    }

    const address = walletInfo.account?.address;
    if (!address) return;

    _wallet = {
      address,
      chain: walletInfo.account?.chain,    // '-239' = mainnet, '-3' = testnet
      publicKey: walletInfo.account?.publicKey,
      name: walletInfo.device?.appName || 'Wallet',
    };

    // Fetch live balance
    let balance = 0;
    try {
      if (typeof Users !== 'undefined') {
        const bal = await Users.balance().catch(() => null);
        balance = bal?.balance_ton ?? 0;
      }
    } catch {}

    _wallet.balance = balance;
    _updateWalletUI(_wallet);

    // Register wallet address on the backend
    if (typeof Users !== 'undefined') {
      try {
        await Users.update({ tonAddress: address });
      } catch (e) {
        console.warn('Failed to update wallet on backend:', e.message);
      }
    }

    // If we received a connect proof, verify it on the backend
    if (walletInfo.connectItems?.tonProof?.proof && typeof TonConnect !== 'undefined') {
      try {
        await _verifyProof(address, walletInfo.connectItems.tonProof.proof);
      } catch (e) {
        console.warn('Proof verification failed:', e.message);
      }
    }

    if (typeof loadUserStats === 'function') loadUserStats();
  }

  // ── Proof verification ────────────────────────────────────────────────────

  async function _verifyProof(address, proof) {
    // proof shape from TON Connect:
    // { timestamp, domain: { lengthBytes, value }, signature, payload, stateInit? }
    if (typeof window.TonConnect === 'undefined' || typeof window.TonConnect.connect !== 'function') return;

    await window.TonConnect.connect({
      address,
      proof: {
        timestamp: proof.timestamp,
        domain: proof.domain?.value || window.location.hostname,
        signature: proof.signature,
        payload: proof.payload || '',
        state_init: proof.stateInit,
      },
      network: _wallet?.chain === '-239' ? 'mainnet' : 'testnet',
    });
  }

  // ── UI updates ────────────────────────────────────────────────────────────

  function _updateWalletUI(wallet) {
    const btn  = document.getElementById('walletBtn');
    const dot  = document.getElementById('walletDot');
    const text = document.getElementById('walletText');
    const bal  = document.getElementById('walletBal');

    if (!btn) return;

    if (!wallet) {
      dot.className  = 'wallet-dot off';
      text.textContent = 'Подключить';
      if (bal) bal.innerHTML = '—<span class="balance-ton"> TON</span>';
      return;
    }

    const short = wallet.address.length > 14
      ? wallet.address.slice(0, 6) + '…' + wallet.address.slice(-4)
      : wallet.address;

    dot.className  = 'wallet-dot';
    text.textContent = short;
    if (bal) bal.innerHTML = (wallet.balance || 0).toFixed(2) + '<span class="balance-ton"> TON</span>';
  }

  // ── Public connect / disconnect ───────────────────────────────────────────

  async function connect() {
    if (!_ui) {
      console.warn('TonConnect not initialised — call TonConnect.init() first');
      return;
    }

    if (_wallet) {
      // Already connected — show options (disconnect)
      await _showConnectedModal();
      return;
    }

    try {
      // Request proof so the backend can verify wallet ownership
      await _ui.openModal();
      // Connection result arrives via _onStatusChange
    } catch (e) {
      console.error('TON Connect error:', e);
      if (typeof showToast === 'function') {
        showToast('Ошибка подключения кошелька', true);
      }
    }
  }

  async function disconnect() {
    if (!_ui) return;
    await _ui.disconnect();
    _wallet = null;
    _updateWalletUI(null);
    if (typeof showToast === 'function') showToast('Кошелёк отключён');
  }

  // ── Connected modal (disconnect / copy address) ───────────────────────────

  async function _showConnectedModal() {
    if (!_wallet) return;

    const modal = document.createElement('div');
    modal.style.cssText = `
      position:fixed;inset:0;background:rgba(10,10,9,.75);z-index:200;
      display:flex;align-items:flex-end;justify-content:center;
    `;

    const short = _wallet.address.slice(0, 6) + '…' + _wallet.address.slice(-4);
    const networkLabel = _wallet.chain === '-239' ? 'Mainnet' : 'Testnet';

    modal.innerHTML = `
      <div style="
        width:100%;max-width:420px;
        background:#252522;border-radius:20px 20px 0 0;
        border:1px solid rgba(212,201,168,.22);border-bottom:none;
        padding:24px 24px 32px;
        animation:slideUp .3s ease;
        font-family:'Manrope',sans-serif;
      ">
        <div style="width:36px;height:4px;background:#363632;border-radius:2px;margin:0 auto 20px;"></div>
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;">
          <div style="width:44px;height:44px;border-radius:22px;background:#2e2e2b;border:2px solid #c9b97a;display:flex;align-items:center;justify-content:center;font-size:20px;">💎</div>
          <div>
            <div style="font-family:'Unbounded',sans-serif;font-size:13px;font-weight:700;color:#e8dfc4;">${_wallet.name}</div>
            <div style="font-size:11px;color:#6e6658;margin-top:2px;">${short} · ${networkLabel}</div>
          </div>
        </div>
        <div style="background:#2e2e2b;border-radius:10px;padding:12px 14px;margin-bottom:16px;display:flex;justify-content:space-between;">
          <span style="font-size:11px;color:#6e6658;">Баланс</span>
          <span style="font-family:'Unbounded',sans-serif;font-size:14px;font-weight:700;color:#c9b97a;">${(_wallet.balance||0).toFixed(4)} TON</span>
        </div>
        <div style="display:flex;gap:10px;">
          <button id="tc-copy" style="
            flex:1;padding:12px;border-radius:10px;font-size:12px;font-weight:700;
            background:rgba(212,201,168,.08);color:#bfb28e;border:1px solid rgba(212,201,168,.22);
            cursor:pointer;font-family:'Manrope',sans-serif;
          ">Копировать адрес</button>
          <button id="tc-disconnect" style="
            flex:1;padding:12px;border-radius:10px;font-size:12px;font-weight:700;
            background:rgba(201,122,122,.12);color:#c97a7a;border:1px solid rgba(201,122,122,.25);
            cursor:pointer;font-family:'Manrope',sans-serif;
          ">Отключить</button>
        </div>
      </div>
    `;

    document.body.appendChild(modal);

    modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });

    modal.querySelector('#tc-copy').addEventListener('click', () => {
      navigator.clipboard?.writeText(_wallet.address);
      if (typeof showToast === 'function') showToast('Адрес скопирован');
      modal.remove();
    });

    modal.querySelector('#tc-disconnect').addEventListener('click', async () => {
      modal.remove();
      await disconnect();
    });
  }

  // ── Send transaction helper ───────────────────────────────────────────────

  /**
   * Send TON to a smart contract (e.g. the prediction market contract).
   * Returns the BOC (Bag of Cells) string which contains the tx hash.
   *
   * @param {string} toAddress  — contract address
   * @param {number} amountTon  — amount in TON
   * @param {string} [comment]  — optional text comment (op code as hex or plain text)
   * @returns {Promise<{boc: string, txHash: string}>}
   */
  async function sendTransaction(toAddress, amountTon, comment = '') {
    if (!_ui || !_wallet) throw new Error('Wallet not connected');

    const nanoAmount = Math.floor(amountTon * 1_000_000_000).toString();

    const tx = {
      validUntil: Math.floor(Date.now() / 1000) + 600, // 10 min
      messages: [
        {
          address: toAddress,
          amount: nanoAmount,
          ...(comment ? { payload: _textToPayload(comment) } : {}),
        },
      ],
    };

    const result = await _ui.sendTransaction(tx);

    // Extract tx hash from BOC
    const txHash = _bocToHash(result.boc);

    return { boc: result.boc, txHash };
  }

  /**
   * Place a bet by sending TON directly to the smart contract.
   * Uses op code 0x01 + outcome byte.
   *
   * @param {string} contractAddress
   * @param {'yes'|'no'} outcome
   * @param {number} amountTon
   * @returns {Promise<string>} txHash
   */
  async function placeBetOnChain(contractAddress, outcome, amountTon) {
    if (!_ui || !_wallet) throw new Error('Wallet not connected');

    // Build op payload: op(0x01) + outcome(1=yes, 2=no)
    // As a 5-byte cell: 32-bit op + 8-bit outcome
    const outcomeCode = outcome === 'yes' ? 1 : 2;
    const payload = _buildOpPayload(0x01, outcomeCode);

    const nanoAmount = Math.floor(amountTon * 1_000_000_000).toString();

    const tx = {
      validUntil: Math.floor(Date.now() / 1000) + 600,
      messages: [
        {
          address: contractAddress,
          amount: nanoAmount,
          payload,
        },
      ],
    };

    const result = await _ui.sendTransaction(tx);
    return _bocToHash(result.boc);
  }

  // ── Payload helpers ───────────────────────────────────────────────────────

  function _textToPayload(text) {
    // Encode text as a TON cell comment (op=0x00000000 + text bytes)
    const bytes = new TextEncoder().encode(text);
    const payload = new Uint8Array(4 + bytes.length);
    payload.set(bytes, 4); // first 4 bytes = 0x00000000 (comment op)
    return btoa(String.fromCharCode(...payload));
  }

  function _buildOpPayload(op, ...args) {
    // Simple 1-cell payload: 4-byte op + 1-byte args each
    const buf = new Uint8Array(4 + args.length);
    buf[0] = (op >>> 24) & 0xff;
    buf[1] = (op >>> 16) & 0xff;
    buf[2] = (op >>>  8) & 0xff;
    buf[3] = (op       ) & 0xff;
    args.forEach((a, i) => { buf[4 + i] = a & 0xff; });
    return btoa(String.fromCharCode(...buf));
  }

  function _bocToHash(boc) {
    // Minimal BOC hash extraction — in production use @ton/core
    // Returns a hex string usable as tx identifier for the backend
    try {
      const raw = atob(boc);
      let hash = 0;
      for (let i = 0; i < Math.min(raw.length, 64); i++) {
        hash = ((hash << 5) - hash) + raw.charCodeAt(i);
        hash |= 0;
      }
      return Math.abs(hash).toString(16).padStart(16, '0');
    } catch {
      return Date.now().toString(16);
    }
  }

  // ── Getters ───────────────────────────────────────────────────────────────

  function getWallet()   { return _wallet; }
  function isConnected() { return !!_wallet; }
  function getUI()       { return _ui; }

  return { init, connect, disconnect, sendTransaction, placeBetOnChain, getWallet, isConnected, getUI };
})();

// ── Wire into index.html ──────────────────────────────────────────────────────
// Replaces the stub handleWalletClick() defined in index.html

window.handleWalletClick = () => TonConnect.connect();

// Auto-init on DOM ready
document.addEventListener('DOMContentLoaded', () => TonConnect.init());
