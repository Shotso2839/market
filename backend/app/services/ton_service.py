"""
TON Blockchain Service

Handles:
- TON Connect wallet address verification
- Transaction lookup and confirmation via toncenter.com API
- Contract state reading (yes_pool, no_pool, status)
- Building messages to send to the smart contract

TON HTTP API docs: https://toncenter.com/api/v2/
"""

import hashlib
import hmac
import time
import httpx
from typing import Optional
from datetime import datetime

from app.config import settings


class TonApiError(Exception):
    pass


class TonService:
    def __init__(self):
        self.api_url = settings.TON_API_URL
        self.api_key = settings.TON_API_KEY
        self._client: Optional[httpx.AsyncClient] = None

    async def _client_instance(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.api_url,
                headers={"X-API-Key": self.api_key} if self.api_key else {},
                timeout=15.0,
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()

    # ── Wallet Verification ────────────────────────────────────────────────────

    def verify_ton_connect_proof(
        self,
        address: str,
        domain: str,
        timestamp: int,
        signature: str,
        payload: str,
        state_init: Optional[str] = None,
    ) -> bool:
        """
        Verify TON Connect v2 proof.
        Spec: https://docs.ton.org/develop/dapps/ton-connect/sign

        In production replace this stub with full Ed25519 verification
        using the public key extracted from the wallet's state_init.
        """
        # Guard: reject stale proofs (> 5 minutes)
        if abs(time.time() - timestamp) > 300:
            return False

        # TODO: Full implementation requires:
        # 1. Parse state_init to extract public key
        # 2. Reconstruct message = domain_len + domain + timestamp + address + payload
        # 3. Prepend magic prefix: "ton-proof-item-v2/"
        # 4. sha256 hash, then verify Ed25519 signature against public key
        #
        # Use PyNaCl: nacl.signing.VerifyKey(pubkey).verify(msg_hash, sig_bytes)

        # For development / demo: accept any proof
        return True

    # ── Transaction Lookup ─────────────────────────────────────────────────────

    async def get_transaction(self, tx_hash: str) -> Optional[dict]:
        """
        Fetch transaction by hash from toncenter API.
        Returns raw transaction dict or None if not found yet.
        """
        client = await self._client_instance()
        try:
            resp = await client.get(
                "/transactions",
                params={"hash": tx_hash, "limit": 1},
            )
            resp.raise_for_status()
            data = resp.json()
            txs = data.get("result", [])
            return txs[0] if txs else None
        except httpx.HTTPError as e:
            raise TonApiError(f"TON API error: {e}") from e

    async def verify_transaction(
        self,
        tx_hash: str,
        expected_destination: Optional[str] = None,
        expected_amount_nano: Optional[int] = None,
        tolerance_nano: int = 10_000_000,  # 0.01 TON tolerance for fees
    ) -> dict:
        """
        Verify that a transaction:
        - Exists on chain
        - Went to the correct contract address
        - Contains the expected amount (within tolerance)

        Returns dict with {confirmed: bool, amount: int, from: str, to: str}
        """
        tx = await self.get_transaction(tx_hash)
        if not tx:
            return {"confirmed": False, "reason": "tx_not_found"}

        try:
            msg = tx["in_msg"]
            destination = msg.get("destination", "")
            value = int(msg.get("value", 0))

            if expected_destination and destination != expected_destination:
                return {
                    "confirmed": False,
                    "reason": "wrong_destination",
                    "got": destination,
                    "expected": expected_destination,
                }

            if expected_amount_nano:
                diff = abs(value - expected_amount_nano)
                if diff > tolerance_nano:
                    return {
                        "confirmed": False,
                        "reason": "wrong_amount",
                        "got": value,
                        "expected": expected_amount_nano,
                    }

            # Timestamp from tx
            utime = tx.get("utime", 0)
            confirmed_at = datetime.utcfromtimestamp(utime) if utime else None

            return {
                "confirmed": True,
                "amount": value,
                "from": msg.get("source", ""),
                "to": destination,
                "confirmed_at": confirmed_at,
                "lt": tx.get("transaction_id", {}).get("lt"),
            }

        except (KeyError, TypeError, ValueError) as e:
            return {"confirmed": False, "reason": f"parse_error: {e}"}

    # ── Contract State ─────────────────────────────────────────────────────────

    async def get_contract_state(self, contract_address: str) -> Optional[dict]:
        """
        Read the prediction market smart contract state.

        Expected contract data (FunC struct):
          - status: uint8 (0=open, 1=closed, 2=resolved, 3=cancelled)
          - yes_pool: coins
          - no_pool: coins
          - bet_closes_at: uint32
          - winning_outcome: uint8 (0=none, 1=yes, 2=no)
          - creator_address: MsgAddress
        """
        client = await self._client_instance()
        try:
            resp = await client.post(
                "/runGetMethod",
                json={
                    "address": contract_address,
                    "method": "get_market_data",
                    "stack": [],
                },
            )
            resp.raise_for_status()
            result = resp.json().get("result", {})
            stack = result.get("stack", [])

            if len(stack) < 5:
                return None

            # Stack items are [type, value] pairs
            # Types: 'num' = integer, 'cell' = BoC, 'slice' = slice
            def parse_int(item):
                return int(item[1], 16) if item[0] == "num" else 0

            return {
                "status": parse_int(stack[0]),
                "yes_pool": parse_int(stack[1]),
                "no_pool": parse_int(stack[2]),
                "bet_closes_at": parse_int(stack[3]),
                "winning_outcome": parse_int(stack[4]),
            }
        except httpx.HTTPError as e:
            raise TonApiError(f"Contract state error: {e}") from e

    async def get_wallet_balance(self, address: str) -> int:
        """Return wallet balance in nanoTON."""
        client = await self._client_instance()
        try:
            resp = await client.get("/getAddressBalance", params={"address": address})
            resp.raise_for_status()
            return int(resp.json().get("result", 0))
        except httpx.HTTPError:
            return 0

    # ── Telegram Auth ──────────────────────────────────────────────────────────

    def verify_telegram_init_data(self, init_data: str) -> Optional[dict]:
        """
        Verify Telegram Mini App initData hash.
        https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

        Returns parsed user dict if valid, None otherwise.
        """
        if not settings.TELEGRAM_BOT_TOKEN:
            # Dev mode: skip verification
            import urllib.parse
            params = dict(urllib.parse.parse_qsl(init_data))
            import json
            user_str = params.get("user", "{}")
            return json.loads(user_str)

        import urllib.parse
        import json

        try:
            params = dict(urllib.parse.parse_qsl(init_data))
            received_hash = params.pop("hash", "")

            # Build data-check-string
            data_check = "\n".join(
                f"{k}={v}" for k, v in sorted(params.items())
            )

            # HMAC-SHA256(data-check-string, HMAC-SHA256("WebAppData", bot_token))
            secret = hmac.new(
                b"WebAppData",
                settings.TELEGRAM_BOT_TOKEN.encode(),
                hashlib.sha256,
            ).digest()

            expected_hash = hmac.new(
                secret, data_check.encode(), hashlib.sha256
            ).hexdigest()

            if not hmac.compare_digest(expected_hash, received_hash):
                return None

            # Check timestamp (10 min window)
            auth_date = int(params.get("auth_date", 0))
            if time.time() - auth_date > 600:
                return None

            return json.loads(params.get("user", "{}"))

        except Exception:
            return None


# Singleton
ton_service = TonService()
