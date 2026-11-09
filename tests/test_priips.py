"""Tests for the PRIIPs SRI, performance scenarios and cost calculations."""

from __future__ import annotations

import math
import random
import unittest
from pathlib import Path

from fundreg.models import load_returns
from fundreg.priips import (
    CostStructure,
    ProductInfo,
    build_kid,
    combine,
    cornish_fisher_scenarios,
    costs_over_time,
    credit_risk_measure,
    historical_scenarios,
    market_risk_measure,
    mrm_class_from_vev,
    reduction_in_yield,
    render_html,
    scenario_table,
    stress_scenario,
    summary_risk_indicator,
)

DATA = Path(__file__).resolve().parents[1] / "data"


def gaussian_returns(n: int = 2560, sigma: float = 0.01, mu: float = 0.0003, seed: int = 7):
    rng = random.Random(seed)
    return [math.exp(rng.gauss(mu, sigma)) - 1.0 for _ in range(n)]


class TestMrmBands(unittest.TestCase):
    def test_band_boundaries_are_inclusive_at_the_lower_edge(self) -> None:
        cases = [
            (0.0, 1),
            (0.0049, 1),
            (0.005, 2),
            (0.0499, 2),
            (0.05, 3),
            (0.1199, 3),
            (0.12, 4),
            (0.1999, 4),
            (0.20, 5),
            (0.2999, 5),
            (0.30, 6),
            (0.7999, 6),
            (0.80, 7),
            (5.0, 7),
        ]
        for vev, expected in cases:
            self.assertEqual(mrm_class_from_vev(vev), expected, msg=f"VEV={vev}")

    def test_negative_vev_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            mrm_class_from_vev(-0.01)


class TestSriMatrix(unittest.TestCase):
    def test_matches_annex_ii_table(self) -> None:
        expected = {
            1: (1, 2, 3, 4, 5, 6, 7),
            2: (1, 2, 3, 4, 5, 6, 7),
            3: (3, 3, 3, 4, 5, 6, 7),
            4: (5, 5, 5, 5, 5, 6, 7),
            5: (5, 5, 5, 5, 5, 6, 7),
            6: (6, 6, 6, 6, 6, 6, 7),
        }
        for crm, row in expected.items():
            for index, sri in enumerate(row, start=1):
                self.assertEqual(combine(index, crm), sri, msg=f"MR{index}/CR{crm}")

    def test_credit_risk_never_lowers_the_indicator_below_market_risk_7(self) -> None:
        for crm in range(1, 7):
            self.assertEqual(combine(7, crm), 7)

    def test_out_of_range_classes_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            combine(0, 1)
        with self.assertRaises(ValueError):
            combine(1, 7)


class TestMarketRiskMeasure(unittest.TestCase):
    def test_vev_approximates_realised_volatility_for_a_gaussian_series(self) -> None:
        # With no skew or excess kurtosis the Cornish-Fisher terms vanish and
        # the VEV should land close to the annualised volatility of the series.
        returns = gaussian_returns(sigma=0.01)
        result = market_risk_measure(returns, rhp_years=5, periods_per_year=256)
        annualised_vol = result.volatility * math.sqrt(256)
        self.assertAlmostEqual(result.vev, annualised_vol, delta=0.015)
        self.assertEqual(result.mrm_class, mrm_class_from_vev(result.vev))

    def test_higher_volatility_raises_the_class(self) -> None:
        calm = market_risk_measure(gaussian_returns(sigma=0.002), rhp_years=5)
        wild = market_risk_measure(gaussian_returns(sigma=0.025), rhp_years=5)
        self.assertLess(calm.vev, wild.vev)
        self.assertLess(calm.mrm_class, wild.mrm_class)

    def test_var_is_a_loss_for_a_volatile_series(self) -> None:
        result = market_risk_measure(gaussian_returns(sigma=0.02), rhp_years=5)
        self.assertLess(result.var_log, 0.0)
        self.assertLess(result.var_return, 0.0)
        self.assertAlmostEqual(result.var_return, math.exp(result.var_log) - 1.0)

    def test_log_and_simple_returns_give_the_same_answer(self) -> None:
        simple = gaussian_returns(sigma=0.012)
        logs = [math.log1p(r) for r in simple]
        a = market_risk_measure(simple, rhp_years=5)
        b = market_risk_measure(logs, rhp_years=5, are_log_returns=True)
        self.assertAlmostEqual(a.vev, b.vev, places=12)

    def test_inputs_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            market_risk_measure([0.01, 0.02], rhp_years=5)
        with self.assertRaises(ValueError):
            market_risk_measure(gaussian_returns(100), rhp_years=0)


class TestCreditRiskMeasure(unittest.TestCase):
    def test_a_fund_with_no_obligor_is_class_one(self) -> None:
        result = credit_risk_measure(has_credit_exposure=False)
        self.assertEqual(result.crm_class, 1)
        self.assertIsNone(result.credit_quality_step)

    def test_credit_quality_step_maps_to_a_class(self) -> None:
        for step, expected in ((0, 1), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6)):
            self.assertEqual(credit_risk_measure(step).crm_class, expected)

    def test_short_maturity_improves_the_step(self) -> None:
        result = credit_risk_measure(3, maturity_years=0.5)
        self.assertEqual(result.adjusted_credit_quality_step, 2)
        self.assertEqual(result.crm_class, 2)

    def test_long_maturity_worsens_the_step(self) -> None:
        result = credit_risk_measure(3, maturity_years=15)
        self.assertEqual(result.adjusted_credit_quality_step, 4)
        self.assertEqual(result.crm_class, 4)

    def test_adjustment_is_clamped_at_the_ends_of_the_scale(self) -> None:
        self.assertEqual(credit_risk_measure(0, maturity_years=0.25).crm_class, 1)
        self.assertEqual(credit_risk_measure(6, maturity_years=30).crm_class, 6)

    def test_credit_exposure_without_a_step_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            credit_risk_measure(None, has_credit_exposure=True)
        with self.assertRaises(ValueError):
            credit_risk_measure(9)


class TestScenarios(unittest.TestCase):
    def setUp(self) -> None:
        self.returns = gaussian_returns(sigma=0.011, mu=0.0004)

    def test_scenarios_are_ordered(self) -> None:
        for builder in (historical_scenarios, cornish_fisher_scenarios):
            result = builder(self.returns, rhp_years=5)
            values = [
                result[name].value for name in ("stress", "unfavourable", "moderate", "favourable")
            ]
            self.assertEqual(values, sorted(values), msg=builder.__name__)

    def test_historical_scenarios_are_drawn_from_observed_sub_periods(self) -> None:
        result = historical_scenarios(self.returns, rhp_years=5, periods_per_year=256)
        window = 5 * 256
        cumulative = []
        for start in range(0, len(self.returns) - window + 1):
            value = 1.0
            for r in self.returns[start : start + window]:
                value *= 1 + r
            cumulative.append(value - 1.0)
        self.assertAlmostEqual(result["unfavourable"].total_return, min(cumulative), places=9)
        self.assertAlmostEqual(result["favourable"].total_return, max(cumulative), places=9)

    def test_history_shorter_than_the_holding_period_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            historical_scenarios(gaussian_returns(100), rhp_years=5, periods_per_year=256)

    def test_scenario_table_falls_back_when_history_is_short(self) -> None:
        sets = scenario_table(gaussian_returns(300), rhp_years=5, periods_per_year=256)
        self.assertEqual([s.years for s in sets], [1.0, 5])
        self.assertIn("Cornish-Fisher", sets[-1].methodology)

    def test_table_shows_one_year_and_the_holding_period(self) -> None:
        sets = scenario_table(self.returns, rhp_years=7)
        self.assertEqual([s.years for s in sets], [1.0, 7])
        single = scenario_table(self.returns, rhp_years=1)
        self.assertEqual([s.years for s in single], [1])

    def test_stress_is_worse_than_the_unfavourable_scenario(self) -> None:
        result = historical_scenarios(self.returns, rhp_years=5)
        self.assertLess(result["stress"].value, result["unfavourable"].value)

    def test_annualised_return_reconciles_with_the_total(self) -> None:
        result = historical_scenarios(self.returns, rhp_years=5)
        for scenario in result.rows():
            compounded = (1 + scenario.annualised_return) ** 5 - 1
            self.assertAlmostEqual(compounded, scenario.total_return, places=9)

    def test_stress_scenario_is_a_loss(self) -> None:
        scenario = stress_scenario(self.returns, rhp_years=5)
        self.assertLess(scenario.total_return, 0.0)


class TestCosts(unittest.TestCase):
    def test_no_costs_means_no_reduction_in_yield(self) -> None:
        row = reduction_in_yield(CostStructure(), years=5, gross_return=0.06)
        self.assertAlmostEqual(row.riy, 0.0, places=12)
        self.assertAlmostEqual(row.total_costs, 0.0, places=6)

    def test_ongoing_charge_shows_up_as_reduction_in_yield(self) -> None:
        row = reduction_in_yield(CostStructure(management_fee=0.01), years=5, gross_return=0.06)
        self.assertAlmostEqual(row.riy, 0.01, places=4)

    def test_entry_cost_bites_harder_over_a_short_horizon(self) -> None:
        costs = CostStructure(entry_cost=0.03, management_fee=0.01)
        short = reduction_in_yield(costs, years=1, gross_return=0.05)
        long = reduction_in_yield(costs, years=10, gross_return=0.05)
        self.assertGreater(short.riy, long.riy)

    def test_composition_sums_to_the_total(self) -> None:
        costs = CostStructure(
            entry_cost=0.02,
            exit_cost=0.01,
            management_fee=0.012,
            transaction_cost=0.002,
            performance_fee=0.001,
        )
        row = reduction_in_yield(costs, years=5, gross_return=0.05)
        self.assertAlmostEqual(sum(row.composition.values()), row.riy, places=10)

    def test_costs_over_time_covers_one_year_and_the_holding_period(self) -> None:
        rows = costs_over_time(CostStructure(management_fee=0.01), rhp_years=5, gross_return=0.05)
        self.assertEqual([r.years for r in rows], [1.0, 3.0, 5])

    def test_non_positive_horizon_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            reduction_in_yield(CostStructure(), years=0, gross_return=0.05)


class TestKidAssembly(unittest.TestCase):
    def setUp(self) -> None:
        self.returns = load_returns(DATA / "returns" / "meridian_european_equity.csv")
        self.product = ProductInfo(
            name="Meridian European Equity Fund A EUR Acc",
            isin="LU1234567AB9",
            manufacturer="Meridian Asset Management",
            rhp_years=5,
            objectives="Long term capital growth from European equities.",
            intended_investor="Retail investors with a five year horizon.",
        )
        self.costs = CostStructure(management_fee=0.0105, transaction_cost=0.0018)

    def test_sample_fund_lands_in_risk_class_four(self) -> None:
        kid = build_kid(self.product, self.returns, self.costs)
        self.assertEqual(kid.sri.sri, 4)
        self.assertEqual(kid.sri.market.mrm_class, 4)
        self.assertEqual(kid.sri.credit.crm_class, 1)

    def test_kid_has_both_holding_periods_and_a_cost_table(self) -> None:
        kid = build_kid(self.product, self.returns, self.costs)
        self.assertEqual([s.years for s in kid.scenario_sets], [1.0, 5])
        self.assertTrue(kid.cost_rows)
        self.assertEqual(kid.rhp_scenarios.years, 5)

    def test_renders_to_self_contained_html(self) -> None:
        kid = build_kid(self.product, self.returns, self.costs)
        html = render_html(kid)
        self.assertIn("<!doctype html>", html)
        self.assertIn("Key Information Document", html)
        self.assertIn("Meridian European Equity Fund A EUR Acc", html)
        self.assertNotIn("<script src=", html)

    def test_serialises_to_json_friendly_structures(self) -> None:
        payload = build_kid(self.product, self.returns, self.costs).to_dict()
        self.assertEqual(payload["risk"]["sri"], 4)
        self.assertEqual(len(payload["scenarios"]), 2)
        self.assertIn("costs", payload)


class TestFullSri(unittest.TestCase):
    def test_credit_exposure_can_raise_the_indicator(self) -> None:
        returns = gaussian_returns(sigma=0.002)
        without = summary_risk_indicator(returns, rhp_years=5)
        with_credit = summary_risk_indicator(
            returns, rhp_years=5, credit_quality_step=5, has_credit_exposure=True
        )
        self.assertLess(without.sri, with_credit.sri)

    def test_narrative_mentions_the_class(self) -> None:
        result = summary_risk_indicator(gaussian_returns(), rhp_years=5)
        self.assertIn(f"{result.sri} out of 7", result.narrative)


if __name__ == "__main__":
    unittest.main()
