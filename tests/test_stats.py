"""Tests for the statistics helpers the PRIIPs methodology rests on."""

from __future__ import annotations

import math
import unittest

from fundreg.stats import (
    Z_95,
    Z_97_5,
    Z_99,
    annualise,
    cornish_fisher_quantile,
    excess_kurtosis,
    mean,
    median,
    moments,
    norm_ppf,
    percentile,
    rolling,
    skewness,
    stdev,
)


class TestNormPpf(unittest.TestCase):
    def test_matches_known_quantiles(self) -> None:
        for p, expected in (
            (0.5, 0.0),
            (0.975, 1.959963984540054),
            (0.025, Z_97_5),
            (0.01, Z_99),
            (0.05, Z_95),
            (0.9, 1.2815515655446004),
        ):
            self.assertAlmostEqual(norm_ppf(p), expected, places=10, msg=f"p={p}")

    def test_round_trips_through_the_cdf(self) -> None:
        for p in (0.001, 0.0101, 0.2, 0.49, 0.75, 0.99, 0.9999):
            x = norm_ppf(p)
            back = 0.5 * math.erfc(-x / math.sqrt(2))
            self.assertAlmostEqual(back, p, places=12, msg=f"p={p}")

    def test_rejects_degenerate_probabilities(self) -> None:
        for p in (0.0, 1.0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                norm_ppf(p)


class TestMoments(unittest.TestCase):
    def test_symmetric_series_has_no_skew(self) -> None:
        series = [-2.0, -1.0, 0.0, 1.0, 2.0]
        self.assertAlmostEqual(mean(series), 0.0)
        self.assertAlmostEqual(skewness(series), 0.0)

    def test_population_standard_deviation(self) -> None:
        series = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        self.assertAlmostEqual(stdev(series), 2.0)
        self.assertAlmostEqual(stdev(series, population=False), 2.138089935299395)

    def test_normal_like_series_has_near_zero_excess_kurtosis(self) -> None:
        # A uniform grid has excess kurtosis of about -1.2; what matters is that
        # the function subtracts three, so a mesokurtic series lands near zero.
        constant = [1.0, 1.0, 1.0, 1.0]
        self.assertEqual(excess_kurtosis(constant), 0.0)
        self.assertLess(excess_kurtosis([0.0, 1.0, 2.0, 3.0, 4.0]), 0.0)

    def test_moments_returns_all_four(self) -> None:
        mu, sigma, skew, kurt = moments([0.1, -0.2, 0.3, -0.1, 0.05])
        self.assertAlmostEqual(mu, 0.03)
        self.assertGreater(sigma, 0)
        self.assertIsInstance(skew, float)
        self.assertIsInstance(kurt, float)

    def test_short_series_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            stdev([1.0])
        with self.assertRaises(ValueError):
            skewness([1.0, 2.0])
        with self.assertRaises(ValueError):
            excess_kurtosis([1.0, 2.0, 3.0])


class TestPercentile(unittest.TestCase):
    def test_endpoints_and_median(self) -> None:
        series = [4.0, 1.0, 3.0, 2.0]
        self.assertEqual(percentile(series, 0.0), 1.0)
        self.assertEqual(percentile(series, 1.0), 4.0)
        self.assertEqual(median(series), 2.5)

    def test_interpolates_between_observations(self) -> None:
        self.assertAlmostEqual(percentile([0.0, 10.0], 0.25), 2.5)

    def test_rejects_out_of_range_quantiles(self) -> None:
        with self.assertRaises(ValueError):
            percentile([1.0, 2.0], 1.5)


class TestCornishFisher(unittest.TestCase):
    def test_collapses_to_the_normal_quantile_without_higher_moments(self) -> None:
        self.assertAlmostEqual(cornish_fisher_quantile(Z_97_5, 0.0, 0.0, 250), Z_97_5)

    def test_negative_skew_pushes_the_lower_tail_further_out(self) -> None:
        plain = cornish_fisher_quantile(Z_97_5, 0.0, 0.0, 250)
        skewed = cornish_fisher_quantile(Z_97_5, -0.8, 0.0, 250)
        self.assertLess(skewed, plain)

    def test_adjustment_shrinks_as_the_horizon_lengthens(self) -> None:
        short = cornish_fisher_quantile(Z_97_5, -0.8, 3.0, 20)
        long = cornish_fisher_quantile(Z_97_5, -0.8, 3.0, 2000)
        self.assertLess(abs(long - Z_97_5), abs(short - Z_97_5))


class TestHelpers(unittest.TestCase):
    def test_rolling_windows(self) -> None:
        self.assertEqual(list(rolling([1, 2, 3, 4], 2)), [[1, 2], [2, 3], [3, 4]])
        self.assertEqual(list(rolling([1, 2], 5)), [])

    def test_annualise_inverts_compounding(self) -> None:
        self.assertAlmostEqual(annualise(1.0, 2.0), math.sqrt(2.0) - 1.0)
        self.assertAlmostEqual(annualise(0.05, 1.0), 0.05)


if __name__ == "__main__":
    unittest.main()
