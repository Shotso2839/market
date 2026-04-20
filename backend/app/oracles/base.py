"""
app/oracles/base.py
Базовый интерфейс оракула.

Каждый оракул:
1. Получает market (ORM-объект)
2. Проверяет внешний источник данных
3. Возвращает OracleResult(outcome='yes'|'no'|None, confidence, source_value)

outcome=None означает «данных ещё нет, не разрешать»
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OracleResult:
    outcome: Optional[str]       # 'yes' | 'no' | None (не разрешать пока)
    confidence: float = 1.0      # 0.0–1.0 насколько уверен оракул
    source_value: str = ""       # что именно проверил (например "BTC = $98 450")
    source_name: str = ""        # название источника ("Pyth Network")
    error: Optional[str] = None  # сообщение об ошибке если что-то пошло не так


class BaseOracle(ABC):
    """
    Базовый класс оракула. Подклассы реализуют resolve().

    Параметры рынка передаются через market.oracle_params (JSON-поле в БД).
    Пример oracle_params для крипто-оракула:
      {
        "symbol": "BTC/USD",
        "condition": "above",
        "target": 100000
      }
    """
    name: str = "base"

    @abstractmethod
    async def resolve(self, market) -> OracleResult:
        """
        Проверяет условие рынка.

        Args:
            market: ORM-объект Market с полем oracle_params (dict)

        Returns:
            OracleResult с outcome='yes'|'no'|None
        """
        ...

    def _parse_params(self, market) -> dict:
        """Безопасно читает oracle_params из рынка."""
        params = getattr(market, "oracle_params", None)
        if isinstance(params, dict):
            return params
        return {}
