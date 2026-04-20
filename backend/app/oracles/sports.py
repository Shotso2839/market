"""
app/oracles/sports.py
Оракул для спортивных результатов.

Источники:
  - football-data.org (футбол, бесплатный план: 10 req/мин)
  - theapisports.com / API-Football (футбол, баскетбол, теннис)

oracle_params для рынка:
  {
    "type": "sports",
    "sport": "football",            # "football" | "basketball" | "tennis"
    "provider": "football_data",    # "football_data" | "api_football"
    "match_id": "459023",           # ID матча в системе провайдера
    "question": "home_win",         # "home_win" | "away_win" | "draw" |
                                    # "home_score_over" | "total_over"
    "target": 2.5                   # для вопросов с числом (total_over)
  }

Как найти match_id:
  football-data.org: GET /v4/matches?dateFrom=2026-05-20&dateTo=2026-05-20
  → найди нужный матч, скопируй id

Пример вопроса рынка:
  "Выиграет ли Спартак матч 20 мая?"
  oracle_params = {
    "type": "sports", "sport": "football",
    "provider": "football_data", "match_id": "459023",
    "question": "home_win"
  }
"""
import httpx
import logging
from typing import Optional

from app.oracles.base import BaseOracle, OracleResult
from app.config import settings

log = logging.getLogger(__name__)

# Базовые URL
FOOTBALL_DATA_URL = "https://api.football-data.org/v4"
API_FOOTBALL_URL  = "https://v3.football.api-sports.io"


class SportsOracle(BaseOracle):
    name = "sports"

    async def resolve(self, market) -> OracleResult:
        params   = self._parse_params(market)
        provider = params.get("provider", "football_data")
        match_id = params.get("match_id")
        question = params.get("question", "home_win")
        target   = float(params.get("target", 0))

        if not match_id:
            return OracleResult(outcome=None, error="oracle_params.match_id is required")

        if provider == "football_data":
            return await self._resolve_football_data(match_id, question, target)
        elif provider == "api_football":
            return await self._resolve_api_football(match_id, question, target)
        else:
            return OracleResult(outcome=None, error=f"Unknown provider: {provider}")

    # ── football-data.org ──────────────────────────────────────────────────────

    async def _resolve_football_data(
        self,
        match_id: str,
        question: str,
        target: float,
    ) -> OracleResult:
        api_key = getattr(settings, "FOOTBALL_DATA_API_KEY", "")
        if not api_key:
            return OracleResult(outcome=None, error="FOOTBALL_DATA_API_KEY not set in .env")

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{FOOTBALL_DATA_URL}/matches/{match_id}",
                    headers={"X-Auth-Token": api_key},
                )
                resp.raise_for_status()
                match = resp.json()

        except httpx.HTTPError as e:
            return OracleResult(outcome=None, error=f"football-data.org API error: {e}")

        status = match.get("status", "")

        # Матч ещё не завершён
        if status not in ("FINISHED", "AWARDED"):
            log.info(f"Match {match_id} not finished yet: status={status}")
            return OracleResult(
                outcome=None,
                source_value=f"Match status: {status}",
                source_name="football-data.org",
            )

        score      = match.get("score", {})
        full_time  = score.get("fullTime", {})
        home_goals = full_time.get("home", 0) or 0
        away_goals = full_time.get("away", 0) or 0
        winner     = score.get("winner", "")  # "HOME_TEAM" | "AWAY_TEAM" | "DRAW"

        home = match.get("homeTeam", {}).get("name", "Home")
        away = match.get("awayTeam", {}).get("name", "Away")

        outcome = self._evaluate_football(question, home_goals, away_goals, winner, target)
        return OracleResult(
            outcome=outcome,
            confidence=1.0,
            source_value=f"{home} {home_goals}:{away_goals} {away}",
            source_name="football-data.org",
        )

    # ── api-football.com ───────────────────────────────────────────────────────

    async def _resolve_api_football(
        self,
        match_id: str,
        question: str,
        target: float,
    ) -> OracleResult:
        api_key = getattr(settings, "API_FOOTBALL_KEY", "")
        if not api_key:
            return OracleResult(outcome=None, error="API_FOOTBALL_KEY not set in .env")

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{API_FOOTBALL_URL}/fixtures",
                    headers={"x-apisports-key": api_key},
                    params={"id": match_id},
                )
                resp.raise_for_status()
                data = resp.json()

        except httpx.HTTPError as e:
            return OracleResult(outcome=None, error=f"API-Football error: {e}")

        fixtures = data.get("response", [])
        if not fixtures:
            return OracleResult(outcome=None, error=f"Match {match_id} not found")

        fixture  = fixtures[0]
        status   = fixture["fixture"]["status"]["short"]

        if status not in ("FT", "AET", "PEN"):
            return OracleResult(
                outcome=None,
                source_value=f"Status: {status}",
                source_name="API-Football",
            )

        goals      = fixture.get("goals", {})
        home_goals = goals.get("home", 0) or 0
        away_goals = goals.get("away", 0) or 0

        if home_goals > away_goals:
            winner = "HOME_TEAM"
        elif away_goals > home_goals:
            winner = "AWAY_TEAM"
        else:
            winner = "DRAW"

        home = fixture["teams"]["home"]["name"]
        away = fixture["teams"]["away"]["name"]

        outcome = self._evaluate_football(question, home_goals, away_goals, winner, target)
        return OracleResult(
            outcome=outcome,
            confidence=1.0,
            source_value=f"{home} {home_goals}:{away_goals} {away}",
            source_name="API-Football",
        )

    # ── Оценка исхода ──────────────────────────────────────────────────────────

    def _evaluate_football(
        self,
        question: str,
        home: int,
        away: int,
        winner: str,
        target: float,
    ) -> Optional[str]:
        if question == "home_win":
            return "yes" if winner == "HOME_TEAM" else "no"
        elif question == "away_win":
            return "yes" if winner == "AWAY_TEAM" else "no"
        elif question == "draw":
            return "yes" if winner == "DRAW" else "no"
        elif question == "home_win_or_draw":
            return "yes" if winner in ("HOME_TEAM", "DRAW") else "no"
        elif question == "away_win_or_draw":
            return "yes" if winner in ("AWAY_TEAM", "DRAW") else "no"
        elif question == "total_over":
            return "yes" if (home + away) > target else "no"
        elif question == "total_under":
            return "yes" if (home + away) < target else "no"
        elif question == "home_score_over":
            return "yes" if home > target else "no"
        elif question == "away_score_over":
            return "yes" if away > target else "no"
        elif question == "both_score":
            return "yes" if home > 0 and away > 0 else "no"
        else:
            log.warning(f"Unknown football question: {question}")
            return None
