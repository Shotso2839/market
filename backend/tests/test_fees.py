"""
tests/test_fees.py  —  тесты динамической fee-модели (Polymarket-style)
Запуск: python tests/test_fees.py
"""
import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.fees import (
    dynamic_taker_fee, peak_fee_pct, fee_at_probability,
    calculate_winner_payout, calculate_resolution_fee,
    summarize_revenue, RevenueEvent, NANO,
    MarketCategory, CATEGORY_THETA,
)

NANO = 1_000_000_000


class TestPeakFee(unittest.TestCase):
    """Пиковые значения при p=0.50 для каждой категории."""

    def test_sports_peak(self):
        # theta=0.030 → peak = 0.030 × 0.25 × 100 = 0.75%
        self.assertAlmostEqual(peak_fee_pct(MarketCategory.SPORTS), 0.75, places=3)

    def test_crypto_peak(self):
        # theta=0.060 → peak = 1.50%
        self.assertAlmostEqual(peak_fee_pct(MarketCategory.CRYPTO), 1.50, places=3)

    def test_politics_peak(self):
        # theta=0.035 → peak = 0.875%
        self.assertAlmostEqual(peak_fee_pct(MarketCategory.POLITICS), 0.875, places=3)

    def test_all_peaks_below_polymarket(self):
        # Наши пики ниже Polymarket для всех категорий
        pm_peaks = {
            MarketCategory.SPORTS:   0.75,
            MarketCategory.CRYPTO:   1.80,
            MarketCategory.POLITICS: 1.00,
            MarketCategory.WEATHER:  1.25,
            MarketCategory.OTHER:    1.25,
        }
        for cat, pm_peak in pm_peaks.items():
            our_peak = peak_fee_pct(cat)
            with self.subTest(cat=cat):
                self.assertLessEqual(our_peak, pm_peak,
                    f"{cat}: наш {our_peak}% должен быть <= PM {pm_peak}%")


class TestFeeFormula(unittest.TestCase):
    """Математика формулы fee = amount × theta × p × (1-p)."""

    def test_max_at_50pct(self):
        """Максимум комиссии строго при p=0.50."""
        amount = 100 * NANO
        cat    = MarketCategory.CRYPTO

        fee_50  = dynamic_taker_fee(amount, 0.50, cat, user_bet_count=1).fee_nano
        fee_40  = dynamic_taker_fee(amount, 0.40, cat, user_bet_count=1).fee_nano
        fee_60  = dynamic_taker_fee(amount, 0.60, cat, user_bet_count=1).fee_nano
        fee_80  = dynamic_taker_fee(amount, 0.80, cat, user_bet_count=1).fee_nano

        self.assertGreater(fee_50, fee_40)
        self.assertGreater(fee_50, fee_60)
        self.assertGreater(fee_50, fee_80)

    def test_symmetry_around_50(self):
        """fee(p) == fee(1-p) — кривая симметрична."""
        amount = 100 * NANO
        cat    = MarketCategory.POLITICS
        for p in [0.20, 0.30, 0.35, 0.45]:
            f1 = fee_at_probability(amount, p,     cat)
            f2 = fee_at_probability(amount, 1 - p, cat)
            with self.subTest(p=p):
                self.assertEqual(f1, f2)

    def test_approaches_zero_at_extremes(self):
        """Комиссия очень мала при p=0.01 и p=0.99."""
        amount = 1000 * NANO  # большая ставка чтобы видеть разницу
        cat    = MarketCategory.CRYPTO
        fee_extreme = fee_at_probability(amount, 0.01, cat)
        fee_50      = fee_at_probability(amount, 0.50, cat)
        # Экстремальная комиссия < 5% от пиковой (реальный ratio ~3.96%)
        self.assertLess(fee_extreme, fee_50 * 0.05)

    def test_crypto_higher_than_sports(self):
        """Крипто дороже спорта при той же сумме и вероятности."""
        amount = 100 * NANO
        fee_crypto = fee_at_probability(amount, 0.50, MarketCategory.CRYPTO)
        fee_sports = fee_at_probability(amount, 0.50, MarketCategory.SPORTS)
        self.assertGreater(fee_crypto, fee_sports)

    def test_effective_rate_at_50pct_crypto(self):
        """Эффективная ставка крипто при 50/50 ≈ 1.5%."""
        result = dynamic_taker_fee(100 * NANO, 0.50, MarketCategory.CRYPTO, user_bet_count=1)
        self.assertAlmostEqual(result.effective_rate_pct, 1.5, delta=0.01)

    def test_effective_rate_at_50pct_sports(self):
        """Эффективная ставка спорт при 50/50 ≈ 0.75%."""
        result = dynamic_taker_fee(100 * NANO, 0.50, MarketCategory.SPORTS, user_bet_count=1)
        self.assertAlmostEqual(result.effective_rate_pct, 0.75, delta=0.01)

    def test_at_90pct_probability(self):
        """При p=0.90 комиссия = theta × 0.09 (допуск 2 nanoTON из-за float)."""
        amount = 100 * NANO
        theta  = CATEGORY_THETA[MarketCategory.CRYPTO]
        expected = int(amount * theta * 0.90 * 0.10)
        result   = fee_at_probability(amount, 0.90, MarketCategory.CRYPTO)
        self.assertAlmostEqual(result, expected, delta=2)


class TestFreeFirstBet(unittest.TestCase):
    """Первая ставка бесплатно."""

    def test_first_bet_zero_fee(self):
        result = dynamic_taker_fee(100 * NANO, 0.50, MarketCategory.CRYPTO,
                                   user_bet_count=0, free_bets=1)
        self.assertTrue(result.is_free)
        self.assertEqual(result.fee_nano, 0)
        self.assertEqual(result.net_amount, 100 * NANO)

    def test_second_bet_has_fee(self):
        result = dynamic_taker_fee(100 * NANO, 0.50, MarketCategory.CRYPTO,
                                   user_bet_count=1, free_bets=1)
        self.assertFalse(result.is_free)
        self.assertGreater(result.fee_nano, 0)

    def test_net_plus_fee_equals_gross(self):
        for prob in [0.20, 0.50, 0.80]:
            result = dynamic_taker_fee(77 * NANO, prob, MarketCategory.SPORTS,
                                       user_bet_count=5)
            with self.subTest(prob=prob):
                self.assertEqual(result.net_amount + result.fee_nano, result.gross_amount)


class TestFeeDistribution(unittest.TestCase):
    """Распределение 80% платформа / 20% создатель."""

    def test_platform_plus_creator_equals_fee(self):
        result = dynamic_taker_fee(100 * NANO, 0.50, MarketCategory.CRYPTO, user_bet_count=3)
        self.assertEqual(result.platform_share + result.creator_share, result.fee_nano)

    def test_creator_gets_20pct(self):
        result = dynamic_taker_fee(1000 * NANO, 0.50, MarketCategory.CRYPTO, user_bet_count=3)
        expected_creator = int(result.fee_nano * 0.20)
        self.assertAlmostEqual(result.creator_share, expected_creator, delta=1)

    def test_platform_gets_80pct(self):
        result = dynamic_taker_fee(1000 * NANO, 0.50, MarketCategory.CRYPTO, user_bet_count=3)
        expected_platform = int(result.fee_nano * 0.80)
        self.assertAlmostEqual(result.platform_share, expected_platform, delta=1)


class TestWinnerPayout(unittest.TestCase):
    """Выплаты победителям — без резолюшн-фи."""

    def test_solo_winner_gets_full_pool(self):
        # Один победитель забирает весь пул
        payout = calculate_winner_payout(10 * NANO, 10 * NANO, 20 * NANO)
        self.assertEqual(payout, 20 * NANO)

    def test_proportional_payout(self):
        # Alice 10 из 60 в winning_pool, total=100 → 10/60 × 100 ≈ 16.67
        alice = calculate_winner_payout(10 * NANO, 60 * NANO, 100 * NANO)
        self.assertAlmostEqual(alice / NANO, 16.67, delta=0.01)

    def test_sum_of_payouts_equals_total(self):
        total    = 100 * NANO
        win_pool =  60 * NANO
        p1 = calculate_winner_payout(10 * NANO, win_pool, total)
        p2 = calculate_winner_payout(20 * NANO, win_pool, total)
        p3 = calculate_winner_payout(30 * NANO, win_pool, total)
        self.assertAlmostEqual(p1 + p2 + p3, total, delta=3)

    def test_no_resolution_fee_at_resolution(self):
        # Resolution fee = 0 в новой модели
        result = calculate_resolution_fee(60 * NANO, 40 * NANO)
        self.assertEqual(result.fee_nano, 0)
        self.assertEqual(result.distributable, 100 * NANO)


class TestEndToEndFlow(unittest.TestCase):
    """
    Полный сценарий: Alice ставит на YES, Bob на NO,
    YES выигрывает, Alice получает весь пул.
    """

    def test_full_flow(self):
        # Alice ставит 10 TON, вероятность YES=55%, категория Крипто
        alice_result = dynamic_taker_fee(
            10 * NANO, 0.55, MarketCategory.CRYPTO, user_bet_count=0  # первая — бесплатно
        )
        self.assertEqual(alice_result.fee_nano, 0)  # первая ставка бесплатно

        # Bob ставит 8 TON, вероятность YES=55% (уже изменилась чуть)
        bob_result = dynamic_taker_fee(
            8 * NANO, 0.55, MarketCategory.CRYPTO, user_bet_count=2
        )
        self.assertGreater(bob_result.fee_nano, 0)

        # Пулы (net суммы)
        yes_pool = alice_result.net_amount  # = 10 TON (fee=0)
        no_pool  = bob_result.net_amount    # = 8 - fee

        total_pool = yes_pool + no_pool

        # YES выигрывает → Alice забирает всё
        alice_payout = calculate_winner_payout(
            yes_pool, yes_pool, total_pool
        )
        # Alice получила больше чем поставила
        self.assertGreater(alice_payout, 10 * NANO)

        # Bob получает 0 (проиграл)
        self.assertEqual(
            calculate_winner_payout(no_pool, no_pool, 0), 0
        )


class TestRevenueSummary(unittest.TestCase):
    def test_empty(self):
        r = summarize_revenue([])
        self.assertEqual(r["total_revenue_ton"], 0)

    def test_totals(self):
        events = [
            RevenueEvent("taker_fee", "m1", 1*NANO, int(0.8*NANO), int(0.2*NANO), "u1"),
            RevenueEvent("taker_fee", "m1", 2*NANO, int(1.6*NANO), int(0.4*NANO), "u2"),
        ]
        r = summarize_revenue(events)
        self.assertAlmostEqual(r["total_revenue_ton"], 3.0, places=4)
        self.assertAlmostEqual(r["platform_revenue_ton"], 2.4, places=4)
        self.assertAlmostEqual(r["creator_revenue_ton"],  0.6, places=4)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    for cls in [
        TestPeakFee, TestFeeFormula, TestFreeFirstBet,
        TestFeeDistribution, TestWinnerPayout,
        TestEndToEndFlow, TestRevenueSummary,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
