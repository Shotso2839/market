"""
app/oracles/crypto.py
Оракул для крипто-рынков через Pyth Network Hermes REST API.

Pyth Network — децентрализованный оракул цен, работает на Solana/EVM/TON.
Hermes API предоставляет актуальные цены без API-ключа.
Документация: https://hermes.pyth.network/docs

oracle_params для рынка:
  {
    "type": "crypto",
    "symbol": "BTC/USD",            # поддерживаемые: BTC, ETH, TON, SOL, BNB и др.
    "condition": "above",           # "above" | "below" | "between"
    "target": 100000,               # целевая цена в USD
    "target_low": 90000,            # только для "between"
    "target_high": 110000           # только для "between"
  }

Пример вопроса рынка:
  "Достигнет ли BTC $100 000 к 1 июня 2026?"
  oracle_params = {"type": "crypto", "symbol": "BTC/USD", "condition": "above", "target": 100000}
"""
import httpx
import logging
from typing import Optional

from app.oracles.base import BaseOracle, OracleResult

log = logging.getLogger(__name__)

HERMES_API = "https://hermes.pyth.network/v2/updates/price/latest"

# Pyth price feed IDs для популярных пар
# Полный список: https://pyth.network/price-feeds
PRICE_FEED_IDS: dict[str, str] = {
    "BTC/USD": "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ETH/USD": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "TON/USD": "0x8963217838ab4cf5cadc172203c1f0b763fbaa45f346d8ee50ba994bbcac3026",
    "SOL/USD": "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    "BNB/USD": "0x2f95862b045670cd22bee3114c39763a4a08beeb663b145d283c31d7d1101c4f",
    "MATIC/USD": "0x5de33a9112c2b700b8d30b8a3402c103578ccfa2765696471cc672bd5cf6ac52",
    "AVAX/USD": "0x93da3352f9f1d105fdfe4971cfa80e9dd777bfc5d0f683ebb6e1294b92137bb7",
    "DOGE/USD": "0xdcef50dd0a4cd2dcc17e45df1676dcb336a11a61c69df7a0299b0150c672d25c",
    "NOT/USD": "0x44f243bc8e5f32b89d5cde2eb9e2e8af69dbc65e6ead60f8ea8c39ecd60d58c8",
}


class CryptoOracle(BaseOracle):
    name = "pyth_crypto"

    async def resolve(self, market) -> OracleResult:
        params = self._parse_params(market)
        symbol    = params.get("symbol", "BTC/USD").upper()
        condition = params.get("condition", "above")
        target    = float(params.get("target", 0))

        feed_id = PRICE_FEED_IDS.get(symbol)
        if not feed_id:
            return OracleResult(
                outcome=None,
                error=f"Unknown symbol: {symbol}. Supported: {list(PRICE_FEED_IDS.keys())}",
            )

        price, conf_interval = await self._fetch_price(feed_id)
        if price is None:
            return OracleResult(outcome=None, error="Failed to fetch price from Pyth")

        log.info(f"Oracle [{symbol}]: price={price:.2f} target={target} condition={condition}")

        # Определяем исход
        outcome = self._evaluate(price, condition, target, params)
        confidence = self._confidence(price, target, conf_interval)

        return OracleResult(
            outcome=outcome,
            confidence=confidence,
            source_value=f"{symbol} = ${price:,.2f} (target: {condition} ${target:,.0f})",
            source_name="Pyth Network",
        )

    async def _fetch_price(self, feed_id: str) -> tuple[Optional[float], float]:
        """Запрашивает актуальную цену через Pyth Hermes REST API."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    HERMES_API,
                    params={"ids[]": feed_id},
                )
                resp.raise_for_status()
                data = resp.json()

                parsed = data["parsed"][0]["price"]
                # Pyth возвращает price как int с exponent
                # price_real = price * 10^exponent
                price_raw = int(parsed["price"])
                exponent  = int(parsed["expo"])
                conf_raw  = int(parsed["conf"])

                price = price_raw * (10 ** exponent)
                conf  = conf_raw  * (10 ** exponent)
                return price, conf

        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            log.warning(f"Pyth API error: {e}")
            return None, 0.0

    def _evaluate(
        self,
        price: float,
        condition: str,
        target: float,
        params: dict,
    ) -> Optional[str]:
        """Вычисляет исход: yes/no/None."""
        if condition == "above":
            return "yes" if price > target else "no"
        elif condition == "below":
            return "yes" if price < target else "no"
        elif condition == "between":
            low  = float(params.get("target_low",  0))
            high = float(params.get("target_high", 0))
            return "yes" if low <= price <= high else "no"
        else:
            log.warning(f"Unknown condition: {condition}")
            return None

    def _confidence(self, price: float, target: float, conf_interval: float) -> float:
        """
        Уверенность 0–1.
        Снижаем если цена слишком близко к целевому порогу (в пределах conf_interval).
        """
        if target == 0:
            return 1.0
        distance = abs(price - target)
        if conf_interval > 0 and distance < conf_interval * 3:
            # Близко к порогу — снижаем уверенность
            return min(0.7, distance / (conf_interval * 3))
        return 1.0
