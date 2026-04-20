"""
app/oracles/__init__.py
Реестр оракулов. Каждый оракул реализует BaseOracle.
"""
from app.oracles.base import BaseOracle, OracleResult
from app.oracles.crypto import CryptoOracle
from app.oracles.sports import SportsOracle
from app.oracles.weather import WeatherOracle

__all__ = ["BaseOracle", "OracleResult", "CryptoOracle", "SportsOracle", "WeatherOracle"]
