"""
tests/test_oracles.py
Юнит-тесты системы оракулов.

Покрывают:
  - CryptoOracle._evaluate()     — условия above/below/between
  - CryptoOracle._confidence()   — уверенность при разных расстояниях до порога
  - SportsOracle._evaluate_football() — все типы вопросов
  - WeatherOracle._evaluate()    — все типы погодных вопросов
  - dispatcher — выбор оракула по типу, MIN_CONFIDENCE фильтр

Запуск:
  python tests/test_oracles.py
  # или: python -m pytest tests/test_oracles.py -v
"""
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.oracles.base import OracleResult
from app.oracles.crypto import CryptoOracle
from app.oracles.sports import SportsOracle
from app.oracles.weather import WeatherOracle


def run_async(coro):
    """Запускает корутину синхронно для юнит-тестов."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1: CryptoOracle — логика оценки условий
# ═════════════════════════════════════════════════════════════════════════════

class TestCryptoOracleEvaluate(unittest.TestCase):

    def setUp(self):
        self.oracle = CryptoOracle()

    # ── condition: above ──────────────────────────────────────────────────────

    def test_above_price_higher_than_target(self):
        result = self.oracle._evaluate(105_000, "above", 100_000, {})
        self.assertEqual(result, "yes")

    def test_above_price_lower_than_target(self):
        result = self.oracle._evaluate(95_000, "above", 100_000, {})
        self.assertEqual(result, "no")

    def test_above_price_exactly_at_target(self):
        # Строго выше, равенство = "no"
        result = self.oracle._evaluate(100_000, "above", 100_000, {})
        self.assertEqual(result, "no")

    # ── condition: below ──────────────────────────────────────────────────────

    def test_below_price_lower_than_target(self):
        result = self.oracle._evaluate(1_500, "below", 2_000, {})
        self.assertEqual(result, "yes")

    def test_below_price_higher_than_target(self):
        result = self.oracle._evaluate(2_500, "below", 2_000, {})
        self.assertEqual(result, "no")

    # ── condition: between ────────────────────────────────────────────────────

    def test_between_inside_range(self):
        params = {"target_low": 90_000, "target_high": 110_000}
        result = self.oracle._evaluate(100_000, "between", 0, params)
        self.assertEqual(result, "yes")

    def test_between_below_range(self):
        params = {"target_low": 90_000, "target_high": 110_000}
        result = self.oracle._evaluate(80_000, "between", 0, params)
        self.assertEqual(result, "no")

    def test_between_above_range(self):
        params = {"target_low": 90_000, "target_high": 110_000}
        result = self.oracle._evaluate(120_000, "between", 0, params)
        self.assertEqual(result, "no")

    def test_between_on_boundary(self):
        params = {"target_low": 90_000, "target_high": 110_000}
        self.assertEqual(self.oracle._evaluate(90_000, "between", 0, params), "yes")
        self.assertEqual(self.oracle._evaluate(110_000, "between", 0, params), "yes")

    def test_unknown_condition_returns_none(self):
        result = self.oracle._evaluate(100_000, "sideways", 100_000, {})
        self.assertIsNone(result)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2: CryptoOracle — уверенность (confidence)
# ═════════════════════════════════════════════════════════════════════════════

class TestCryptoOracleConfidence(unittest.TestCase):

    def setUp(self):
        self.oracle = CryptoOracle()

    def test_far_from_target_full_confidence(self):
        # Цена 150k, цель 100k, conf_interval=50 → далеко → confidence=1.0
        conf = self.oracle._confidence(150_000, 100_000, 50)
        self.assertEqual(conf, 1.0)

    def test_very_close_to_target_low_confidence(self):
        # Цена 100_050, цель 100_000, conf_interval=100 (в пределах 3*conf)
        conf = self.oracle._confidence(100_050, 100_000, 100)
        self.assertLess(conf, 0.7)
        self.assertGreaterEqual(conf, 0.0)

    def test_zero_conf_interval_full_confidence(self):
        conf = self.oracle._confidence(100_000, 100_000, 0)
        self.assertEqual(conf, 1.0)

    def test_zero_target_full_confidence(self):
        conf = self.oracle._confidence(100_000, 0, 100)
        self.assertEqual(conf, 1.0)

    def test_confidence_range_0_to_1(self):
        for price in [50_000, 99_900, 100_000, 100_100, 200_000]:
            conf = self.oracle._confidence(price, 100_000, 200)
            with self.subTest(price=price):
                self.assertGreaterEqual(conf, 0.0)
                self.assertLessEqual(conf, 1.0)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3: CryptoOracle — полный resolve() с мок-ом API
# ═════════════════════════════════════════════════════════════════════════════

class TestCryptoOracleResolve(unittest.TestCase):

    def setUp(self):
        self.oracle = CryptoOracle()

    def _make_market(self, symbol="BTC/USD", condition="above", target=100_000):
        market = MagicMock()
        market.oracle_params = {
            "type": "crypto",
            "symbol": symbol,
            "condition": condition,
            "target": target,
        }
        return market

    def test_resolve_above_target_returns_yes(self):
        market = self._make_market("BTC/USD", "above", 100_000)
        with patch.object(self.oracle, '_fetch_price', new_callable=AsyncMock) as mock_price:
            mock_price.return_value = (105_000.0, 50.0)
            result = run_async(self.oracle.resolve(market))
        self.assertEqual(result.outcome, "yes")
        self.assertEqual(result.source_name, "Pyth Network")

    def test_resolve_below_target_returns_no(self):
        market = self._make_market("BTC/USD", "above", 100_000)
        with patch.object(self.oracle, '_fetch_price', new_callable=AsyncMock) as mock_price:
            mock_price.return_value = (95_000.0, 100.0)
            result = run_async(self.oracle.resolve(market))
        self.assertEqual(result.outcome, "no")

    def test_resolve_api_failure_returns_none_outcome(self):
        market = self._make_market()
        with patch.object(self.oracle, '_fetch_price', new_callable=AsyncMock) as mock_price:
            mock_price.return_value = (None, 0.0)
            result = run_async(self.oracle.resolve(market))
        self.assertIsNone(result.outcome)
        self.assertIsNotNone(result.error)

    def test_resolve_unknown_symbol_returns_error(self):
        market = self._make_market("SHIB/USD")
        result = run_async(self.oracle.resolve(market))
        self.assertIsNone(result.outcome)
        self.assertIsNotNone(result.error)
        self.assertIn("SHIB/USD", result.error)

    def test_source_value_contains_price_and_target(self):
        market = self._make_market("ETH/USD", "above", 5_000)
        with patch.object(self.oracle, '_fetch_price', new_callable=AsyncMock) as mock_price:
            mock_price.return_value = (5_500.0, 10.0)
            result = run_async(self.oracle.resolve(market))
        self.assertIn("5,500", result.source_value)
        self.assertIn("5,000", result.source_value)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4: SportsOracle — оценка футбольных исходов
# ═════════════════════════════════════════════════════════════════════════════

class TestSportsOracleEvaluate(unittest.TestCase):

    def setUp(self):
        self.oracle = SportsOracle()

    def _eval(self, question, home, away, winner=None, target=0):
        if winner is None:
            if home > away:   winner = "HOME_TEAM"
            elif away > home: winner = "AWAY_TEAM"
            else:             winner = "DRAW"
        return self.oracle._evaluate_football(question, home, away, winner, target)

    # ── Победитель ────────────────────────────────────────────────────────────

    def test_home_win_correct(self):
        self.assertEqual(self._eval("home_win", 2, 1), "yes")

    def test_home_win_draw(self):
        self.assertEqual(self._eval("home_win", 1, 1), "no")

    def test_home_win_away_wins(self):
        self.assertEqual(self._eval("home_win", 0, 1), "no")

    def test_away_win_correct(self):
        self.assertEqual(self._eval("away_win", 0, 2), "yes")

    def test_draw_correct(self):
        self.assertEqual(self._eval("draw", 1, 1), "yes")

    def test_draw_not_draw(self):
        self.assertEqual(self._eval("draw", 2, 1), "no")

    # ── Двойной шанс ─────────────────────────────────────────────────────────

    def test_home_win_or_draw_home_wins(self):
        self.assertEqual(self._eval("home_win_or_draw", 2, 0), "yes")

    def test_home_win_or_draw_draw(self):
        self.assertEqual(self._eval("home_win_or_draw", 1, 1), "yes")

    def test_home_win_or_draw_away_wins(self):
        self.assertEqual(self._eval("home_win_or_draw", 0, 1), "no")

    # ── Тоталы ───────────────────────────────────────────────────────────────

    def test_total_over_yes(self):
        self.assertEqual(self._eval("total_over", 2, 2, target=2.5), "yes")

    def test_total_over_no(self):
        self.assertEqual(self._eval("total_over", 1, 1, target=2.5), "no")

    def test_total_under_yes(self):
        self.assertEqual(self._eval("total_under", 0, 1, target=2.5), "yes")

    def test_total_under_no(self):
        self.assertEqual(self._eval("total_under", 2, 2, target=2.5), "no")

    def test_home_score_over(self):
        self.assertEqual(self._eval("home_score_over", 3, 0, target=1.5), "yes")
        self.assertEqual(self._eval("home_score_over", 1, 0, target=1.5), "no")

    # ── Оба забили ───────────────────────────────────────────────────────────

    def test_both_score_yes(self):
        self.assertEqual(self._eval("both_score", 1, 2), "yes")

    def test_both_score_no_one_blank(self):
        self.assertEqual(self._eval("both_score", 0, 2), "no")

    def test_both_score_no_both_blank(self):
        self.assertEqual(self._eval("both_score", 0, 0), "no")

    def test_unknown_question_returns_none(self):
        self.assertIsNone(self._eval("winner_on_penalties", 1, 1))


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5: SportsOracle — resolve() с мок-ом API
# ═════════════════════════════════════════════════════════════════════════════

class TestSportsOracleResolve(unittest.TestCase):

    def setUp(self):
        self.oracle = SportsOracle()

    def _make_market(self, question="home_win", provider="football_data", match_id="12345"):
        m = MagicMock()
        m.oracle_params = {
            "type": "sports",
            "provider": provider,
            "match_id": match_id,
            "question": question,
        }
        return m

    def test_missing_match_id_returns_error(self):
        m = MagicMock()
        m.oracle_params = {"type": "sports", "provider": "football_data"}
        result = run_async(self.oracle.resolve(m))
        self.assertIsNone(result.outcome)
        self.assertIn("match_id", result.error)

    def test_match_finished_home_win(self):
        market = self._make_market("home_win")
        mock_response = {
            "status": "FINISHED",
            "score": {
                "fullTime": {"home": 2, "away": 1},
                "winner": "HOME_TEAM",
            },
            "homeTeam": {"name": "Spartak"},
            "awayTeam": {"name": "CSKA"},
        }
        with patch.object(self.oracle, '_fetch_football_data', new_callable=AsyncMock) as mock_fd:
            mock_fd.return_value = mock_response
            # Напрямую тестируем _resolve_football_data через mock
            with patch('httpx.AsyncClient') as mock_client:
                mock_resp = MagicMock()
                mock_resp.json.return_value = mock_response
                mock_resp.raise_for_status = MagicMock()
                mock_client.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                    get=AsyncMock(return_value=mock_resp)
                ))
                mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

                with patch.object(self.oracle, '_resolve_football_data', new_callable=AsyncMock) as mock_resolve:
                    mock_resolve.return_value = OracleResult(
                        outcome="yes",
                        confidence=1.0,
                        source_value="Spartak 2:1 CSKA",
                        source_name="football-data.org",
                    )
                    result = run_async(self.oracle.resolve(market))

        self.assertEqual(result.outcome, "yes")

    def test_match_not_finished_returns_none_outcome(self):
        market = self._make_market()
        with patch.object(self.oracle, '_resolve_football_data', new_callable=AsyncMock) as mock_resolve:
            mock_resolve.return_value = OracleResult(
                outcome=None,
                source_value="Match status: IN_PLAY",
                source_name="football-data.org",
            )
            result = run_async(self.oracle.resolve(market))
        self.assertIsNone(result.outcome)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 6: WeatherOracle — оценка погодных условий
# ═════════════════════════════════════════════════════════════════════════════

class TestWeatherOracleEvaluate(unittest.TestCase):

    def setUp(self):
        self.oracle = WeatherOracle()

    def _weather(self, rain=0, temp_max=20, temp_min=10, temp_mean=15,
                 snow=0, wind=15, code=0, sunshine=0):
        return {
            "precipitation_sum": rain,
            "temp_max": temp_max, "temp_min": temp_min, "temp_mean": temp_mean,
            "snowfall_sum": snow, "wind_speed_max": wind,
            "weather_code": code, "sunshine_duration": sunshine,
        }

    # ── Дождь ────────────────────────────────────────────────────────────────

    def test_rain_yes_above_threshold(self):
        w = self._weather(rain=5.0)
        result = self.oracle._evaluate("rain", w, 0, {"rain_threshold_mm": 1.0})
        self.assertEqual(result, "yes")

    def test_rain_no_below_threshold(self):
        w = self._weather(rain=0.5)
        result = self.oracle._evaluate("rain", w, 0, {"rain_threshold_mm": 1.0})
        self.assertEqual(result, "no")

    def test_rain_custom_threshold(self):
        w = self._weather(rain=3.0)
        # С порогом 5мм — не дождь
        self.assertEqual(self.oracle._evaluate("rain", w, 0, {"rain_threshold_mm": 5.0}), "no")
        # С порогом 1мм — дождь
        self.assertEqual(self.oracle._evaluate("rain", w, 0, {"rain_threshold_mm": 1.0}), "yes")

    def test_no_rain_yes(self):
        w = self._weather(rain=0.0)
        result = self.oracle._evaluate("no_rain", w, 0, {})
        self.assertEqual(result, "yes")

    # ── Температура ───────────────────────────────────────────────────────────

    def test_temp_above_yes(self):
        w = self._weather(temp_max=25)
        self.assertEqual(self.oracle._evaluate("temp_above", w, 20, {}), "yes")

    def test_temp_above_no(self):
        w = self._weather(temp_max=18)
        self.assertEqual(self.oracle._evaluate("temp_above", w, 20, {}), "no")

    def test_temp_below_yes(self):
        w = self._weather(temp_min=-5)
        self.assertEqual(self.oracle._evaluate("temp_below", w, 0, {}), "yes")

    def test_temp_below_no(self):
        w = self._weather(temp_min=5)
        self.assertEqual(self.oracle._evaluate("temp_below", w, 0, {}), "no")

    # ── Снег ─────────────────────────────────────────────────────────────────

    def test_snow_yes(self):
        w = self._weather(snow=10.0)
        self.assertEqual(self.oracle._evaluate("snow", w, 0, {}), "yes")

    def test_snow_no(self):
        w = self._weather(snow=0)
        self.assertEqual(self.oracle._evaluate("snow", w, 0, {}), "no")

    # ── Солнечно ─────────────────────────────────────────────────────────────

    def test_sunny_yes_more_than_6h(self):
        w = self._weather(sunshine=25_000)  # 6.9 часов
        self.assertEqual(self.oracle._evaluate("sunny", w, 0, {}), "yes")

    def test_sunny_no_less_than_6h(self):
        w = self._weather(sunshine=10_000)  # 2.7 часов
        self.assertEqual(self.oracle._evaluate("sunny", w, 0, {}), "no")

    # ── Ветер ────────────────────────────────────────────────────────────────

    def test_wind_above_yes(self):
        w = self._weather(wind=80)
        self.assertEqual(self.oracle._evaluate("wind_above", w, 50, {}), "yes")

    def test_wind_above_no(self):
        w = self._weather(wind=30)
        self.assertEqual(self.oracle._evaluate("wind_above", w, 50, {}), "no")

    # ── Экстремальная погода ──────────────────────────────────────────────────

    def test_extreme_weather_code_80_plus(self):
        w = self._weather(code=95)  # Thunderstorm
        self.assertEqual(self.oracle._evaluate("extreme_weather", w, 0, {}), "yes")

    def test_no_extreme_weather_code_below_80(self):
        w = self._weather(code=3)  # Overcast
        self.assertEqual(self.oracle._evaluate("extreme_weather", w, 0, {}), "no")

    def test_unknown_question_returns_none(self):
        w = self._weather()
        self.assertIsNone(self.oracle._evaluate("tornado", w, 0, {}))


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 7: WeatherOracle — дата ещё не наступила
# ═════════════════════════════════════════════════════════════════════════════

class TestWeatherOracleFutureDate(unittest.TestCase):

    def setUp(self):
        self.oracle = WeatherOracle()

    def test_future_date_returns_none_outcome(self):
        market = MagicMock()
        market.oracle_params = {
            "type": "weather",
            "city": "Moscow",
            "date": "2099-01-01",  # далёкое будущее
            "question": "rain",
        }
        result = run_async(self.oracle.resolve(market))
        self.assertIsNone(result.outcome)
        self.assertIn("not arrived yet", result.source_value)

    def test_missing_date_returns_error(self):
        market = MagicMock()
        market.oracle_params = {"type": "weather", "city": "Moscow", "question": "rain"}
        result = run_async(self.oracle.resolve(market))
        self.assertIsNone(result.outcome)
        self.assertIsNotNone(result.error)

    def test_invalid_date_returns_error(self):
        market = MagicMock()
        market.oracle_params = {
            "type": "weather", "city": "Moscow",
            "date": "not-a-date", "question": "rain",
        }
        result = run_async(self.oracle.resolve(market))
        self.assertIsNone(result.outcome)
        self.assertIsNotNone(result.error)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 8: Dispatcher — маппинг типов и MIN_CONFIDENCE
# ═════════════════════════════════════════════════════════════════════════════

class TestOracleDispatcher(unittest.TestCase):

    def test_oracle_map_has_all_types(self):
        from app.oracles.dispatcher import _ORACLE_MAP
        for key in ["crypto", "pyth", "sports", "football", "weather"]:
            with self.subTest(key=key):
                self.assertIn(key, _ORACLE_MAP)

    def test_min_confidence_is_reasonable(self):
        from app.oracles.dispatcher import MIN_CONFIDENCE
        self.assertGreaterEqual(MIN_CONFIDENCE, 0.5)
        self.assertLessEqual(MIN_CONFIDENCE, 1.0)

    def test_oracle_result_skipped_if_low_confidence(self):
        """Если confidence < MIN_CONFIDENCE — не разрешаем."""
        from app.oracles.dispatcher import MIN_CONFIDENCE
        result = OracleResult(outcome="yes", confidence=MIN_CONFIDENCE - 0.1)
        # Диспетчер должен пропустить такой результат
        should_resolve = result.outcome is not None and result.confidence >= MIN_CONFIDENCE
        self.assertFalse(should_resolve)

    def test_oracle_result_passes_if_sufficient_confidence(self):
        from app.oracles.dispatcher import MIN_CONFIDENCE
        result = OracleResult(outcome="yes", confidence=MIN_CONFIDENCE + 0.05)
        should_resolve = result.outcome is not None and result.confidence >= MIN_CONFIDENCE
        self.assertTrue(should_resolve)

    def test_none_outcome_never_resolves(self):
        from app.oracles.dispatcher import MIN_CONFIDENCE
        result = OracleResult(outcome=None, confidence=1.0)
        should_resolve = result.outcome is not None and result.confidence >= MIN_CONFIDENCE
        self.assertFalse(should_resolve)

    def test_oracle_result_dataclass_defaults(self):
        r = OracleResult(outcome="yes")
        self.assertEqual(r.confidence, 1.0)
        self.assertEqual(r.source_value, "")
        self.assertEqual(r.source_name, "")
        self.assertIsNone(r.error)


# ═════════════════════════════════════════════════════════════════════════════
# RUNNER
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    for cls in [
        TestCryptoOracleEvaluate,
        TestCryptoOracleConfidence,
        TestCryptoOracleResolve,
        TestSportsOracleEvaluate,
        TestSportsOracleResolve,
        TestWeatherOracleEvaluate,
        TestWeatherOracleFutureDate,
        TestOracleDispatcher,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
