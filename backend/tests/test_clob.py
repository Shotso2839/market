"""
tests/test_clob.py  —  тесты CLOB/LMSR движка
Запуск: python tests/test_clob.py
"""
import sys, os, math, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.clob import (
    CLOBMarket, Outcome, buy_shares, sell_shares,
    cost_to_buy, cost_to_sell, shares_for_ton,
    resolve_market, payout_for_shares, price_impact,
)


def make_market(b=100.0) -> CLOBMarket:
    return CLOBMarket(market_id="test", b=b)


class TestInitialState(unittest.TestCase):
    def test_initial_price_50pct(self):
        m = make_market()
        self.assertAlmostEqual(m.price_yes, 0.5, places=6)
        self.assertAlmostEqual(m.price_no,  0.5, places=6)

    def test_prices_sum_to_one(self):
        m = make_market()
        self.assertAlmostEqual(m.price_yes + m.price_no, 1.0, places=10)


class TestBuyShares(unittest.TestCase):
    def test_buy_yes_increases_yes_price(self):
        m = make_market()
        buy_shares(m, Outcome.YES, 10.0)
        self.assertGreater(m.price_yes, 0.5)

    def test_buy_no_increases_no_price(self):
        m = make_market()
        buy_shares(m, Outcome.NO, 10.0)
        self.assertGreater(m.price_no, 0.5)

    def test_prices_always_sum_to_one_after_buy(self):
        m = make_market()
        buy_shares(m, Outcome.YES, 50.0)
        self.assertAlmostEqual(m.price_yes + m.price_no, 1.0, places=10)

    def test_buy_returns_positive_shares(self):
        m = make_market()
        shares, cost = buy_shares(m, Outcome.YES, 10.0)
        self.assertGreater(shares, 0)
        self.assertAlmostEqual(cost, 10.0, delta=0.001)

    def test_larger_buy_gets_fewer_shares_per_ton(self):
        """Price impact: большая покупка даёт хуже среднюю цену."""
        m1 = make_market(); m2 = make_market()
        s1, _ = buy_shares(m1, Outcome.YES, 10.0)
        s2, _ = buy_shares(m2, Outcome.YES, 100.0)
        ratio_small = s1 / 10.0
        ratio_large = s2 / 100.0
        self.assertGreater(ratio_small, ratio_large)


class TestSellShares(unittest.TestCase):
    def test_sell_returns_ton(self):
        m = make_market()
        shares, _ = buy_shares(m, Outcome.YES, 10.0)
        proceeds = sell_shares(m, Outcome.YES, shares)
        self.assertGreater(proceeds, 0)

    def test_buy_then_sell_small_loss(self):
        """Купил и сразу продал — небольшой убыток из-за price impact."""
        m = make_market()
        shares, cost = buy_shares(m, Outcome.YES, 10.0)
        proceeds = sell_shares(m, Outcome.YES, shares)
        # Получили чуть меньше чем вложили (price impact)
        self.assertLessEqual(proceeds, cost)
        # Но не больше 5% потери
        self.assertGreater(proceeds, cost * 0.95)

    def test_price_reverts_after_buy_sell(self):
        """Купил и продал — цена возвращается к исходной."""
        m = make_market()
        price_before = m.price_yes
        shares, _ = buy_shares(m, Outcome.YES, 20.0)
        sell_shares(m, Outcome.YES, shares)
        self.assertAlmostEqual(m.price_yes, price_before, places=6)

    def test_sell_lowers_yes_price(self):
        m = make_market()
        buy_shares(m, Outcome.YES, 50.0)
        price_after_buy = m.price_yes
        shares = m.q_yes / 2
        sell_shares(m, Outcome.YES, shares)
        self.assertLess(m.price_yes, price_after_buy)


class TestExitAtAnyTime(unittest.TestCase):
    """Главное свойство: можно войти и выйти в любой момент."""

    def test_exit_when_winning(self):
        """
        Alice купила YES когда вероятность была 50%.
        Вероятность выросла до ~70%. Alice продаёт с прибылью.
        """
        m = make_market(b=100.0)
        # Alice входит
        alice_shares, alice_cost = buy_shares(m, Outcome.YES, 10.0)
        price_at_entry = 0.5

        # Рынок движется в пользу YES — другие покупают YES
        buy_shares(m, Outcome.YES, 80.0)
        price_now = m.price_yes
        self.assertGreater(price_now, 0.6)  # цена выросла

        # Alice выходит
        alice_proceeds = sell_shares(m, Outcome.YES, alice_shares)

        # Alice в прибыли
        self.assertGreater(alice_proceeds, alice_cost)

    def test_exit_when_losing(self):
        """
        Bob купил NO. Рынок пошёл против него.
        Bob выходит с убытком но не теряет всё.
        """
        m = make_market(b=100.0)
        bob_shares, bob_cost = buy_shares(m, Outcome.NO, 10.0)

        # Рынок идёт против Bob — YES покупают
        buy_shares(m, Outcome.YES, 100.0)

        bob_proceeds = sell_shares(m, Outcome.NO, bob_shares)

        # Bob потерял часть но не всё
        self.assertLess(bob_proceeds, bob_cost)
        self.assertGreater(bob_proceeds, 0)

    def test_multiple_entries_exits(self):
        """Несколько входов и выходов — рынок стабилен."""
        m = make_market(b=200.0)
        positions = []

        for i in range(5):
            amt = 10.0 + i * 5
            shares, cost = buy_shares(m, Outcome.YES, amt)
            positions.append((shares, cost))

        # Частично закрываем
        for shares, cost in positions[:3]:
            proceeds = sell_shares(m, Outcome.YES, shares)
            self.assertGreater(proceeds, 0)

        # Рынок продолжает работать
        self.assertAlmostEqual(m.price_yes + m.price_no, 1.0, places=10)


class TestResolveAndPayout(unittest.TestCase):
    def test_winner_gets_one_ton_per_share(self):
        m = make_market()
        shares, _ = buy_shares(m, Outcome.YES, 10.0)
        resolve_market(m, Outcome.YES)
        payout = payout_for_shares(m, Outcome.YES, shares)
        self.assertAlmostEqual(payout, shares, places=10)

    def test_loser_gets_zero(self):
        m = make_market()
        shares, _ = buy_shares(m, Outcome.NO, 10.0)
        resolve_market(m, Outcome.YES)
        payout = payout_for_shares(m, Outcome.NO, shares)
        self.assertEqual(payout, 0.0)

    def test_winner_gets_more_than_invested_when_minority(self):
        """
        Инвестировал в правильный исход когда он был маловероятным → профит.
        """
        m = make_market(b=100.0)
        # Большинство ставит на NO
        buy_shares(m, Outcome.NO, 200.0)
        # Alice ставит на YES (сейчас ~20%)
        alice_shares, alice_cost = buy_shares(m, Outcome.YES, 10.0)
        # YES побеждает
        resolve_market(m, Outcome.YES)
        payout = payout_for_shares(m, Outcome.YES, alice_shares)
        # Alice получила больше шеров чем вложила TON (вход по низкой цене)
        self.assertGreater(payout, alice_cost)

    def test_cannot_payout_before_resolution(self):
        m = make_market()
        shares, _ = buy_shares(m, Outcome.YES, 5.0)
        payout = payout_for_shares(m, Outcome.YES, shares)
        self.assertEqual(payout, 0.0)  # рынок не разрешён


class TestPriceImpact(unittest.TestCase):
    def test_price_impact_structure(self):
        m = make_market()
        result = price_impact(m, Outcome.YES, 10.0)
        for key in ['shares','cost_ton','price_before','price_after','avg_price','potential_win']:
            self.assertIn(key, result)

    def test_price_impact_positive(self):
        m = make_market()
        result = price_impact(m, Outcome.YES, 100.0)
        self.assertGreater(result['price_after'], result['price_before'])

    def test_small_trade_low_impact(self):
        m = make_market(b=1000.0)  # высокая ликвидность
        result = price_impact(m, Outcome.YES, 1.0)
        self.assertLess(result['price_impact'], 0.01)  # < 1% сдвига

    def test_large_trade_high_impact(self):
        m = make_market(b=10.0)   # низкая ликвидность
        result = price_impact(m, Outcome.YES, 50.0)
        self.assertGreater(result['price_impact'], 0.05)  # > 5% сдвига


class TestLiquidityParameter(unittest.TestCase):
    def test_higher_b_less_price_impact(self):
        """Чем больше b, тем меньше влияние одной сделки на цену."""
        m_low  = make_market(b=10.0)
        m_high = make_market(b=1000.0)

        r_low  = price_impact(m_low,  Outcome.YES, 20.0)
        r_high = price_impact(m_high, Outcome.YES, 20.0)

        self.assertGreater(r_low['price_impact'], r_high['price_impact'])


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    for cls in [
        TestInitialState, TestBuyShares, TestSellShares,
        TestExitAtAnyTime, TestResolveAndPayout,
        TestPriceImpact, TestLiquidityParameter,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
