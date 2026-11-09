"""Tests for the UCITS limit suite and the commitment-approach exposure."""

from __future__ import annotations

import unittest
from pathlib import Path

from fundreg.findings import Severity
from fundreg.models import (
    AssetClass,
    Derivative,
    Instrument,
    Portfolio,
    Position,
    load_portfolio,
)
from fundreg.ucits import (
    UcitsConfig,
    check_portfolio,
    commitment_approach_exposure,
    counterparty_exposures,
)

DATA = Path(__file__).resolve().parents[1] / "data"


def security(
    issuer: str,
    value: float,
    asset_class: AssetClass = AssetClass.EQUITY,
    **kwargs,
) -> Position:
    instrument = Instrument(
        instrument_id=f"{issuer}_{asset_class.value}",
        name=issuer,
        asset_class=asset_class,
        issuer_id=issuer,
        issuer_name=issuer,
        **kwargs,
    )
    return Position(instrument=instrument, quantity=1.0, market_value=value)


def otc(
    name: str,
    *,
    notional: float,
    mtm: float,
    underlying: str,
    counterparty: str | None = None,
    collateral: float = 0.0,
    netting_set: str | None = None,
    look_through: bool = True,
    issuer: str | None = None,
) -> Position:
    instrument = Instrument(
        instrument_id=name,
        name=name,
        asset_class=AssetClass.OTC_DERIVATIVE,
        issuer_id=issuer or underlying,
        look_through_to_issuer=look_through,
        derivative=Derivative(
            underlying_id=underlying,
            notional=notional,
            counterparty=counterparty,
            collateral_received=collateral,
            netting_set=netting_set,
        ),
    )
    return Position(instrument=instrument, quantity=1.0, market_value=mtm)


def portfolio(*positions: Position, nav: float = 1_000_000.0, borrowing: float = 0.0) -> Portfolio:
    return Portfolio(fund_name="Test Fund", nav=nav, positions=list(positions), borrowing=borrowing)


def finding(result, rule_id: str, subject_contains: str = ""):
    for f in result:
        if f.rule_id == rule_id and subject_contains in f.subject:
            return f
    raise AssertionError(f"no finding {rule_id} with subject containing {subject_contains!r}")


class TestIssuerConcentration(unittest.TestCase):
    def test_position_above_the_single_issuer_limit_breaches(self) -> None:
        result = check_portfolio(portfolio(security("ACME", 120_000)))
        f = finding(result, "UCITS.ART52.1A.SINGLE_ISSUER", "ACME")
        self.assertEqual(f.severity, Severity.BREACH)
        self.assertAlmostEqual(f.actual, 12.0)
        self.assertEqual(f.limit, 10.0)

    def test_the_five_percent_option_can_be_selected(self) -> None:
        config = UcitsConfig(single_issuer_limit=5.0)
        result = check_portfolio(portfolio(security("ACME", 70_000)), config)
        self.assertEqual(
            finding(result, "UCITS.ART52.1A.SINGLE_ISSUER", "ACME").severity, Severity.BREACH
        )

    def test_early_warning_fires_below_the_limit(self) -> None:
        result = check_portfolio(portfolio(security("ACME", 95_000)))
        self.assertEqual(
            finding(result, "UCITS.ART52.1A.SINGLE_ISSUER", "ACME").severity, Severity.WARNING
        )

    def test_positions_in_one_issuer_are_aggregated_across_instruments(self) -> None:
        result = check_portfolio(
            portfolio(
                security("ACME", 60_000, AssetClass.EQUITY),
                security("ACME", 60_000, AssetClass.CORPORATE_BOND),
            )
        )
        self.assertAlmostEqual(finding(result, "UCITS.ART52.1A.SINGLE_ISSUER", "ACME").actual, 12.0)

    def test_forty_percent_aggregate_counts_only_issuers_above_the_trigger(self) -> None:
        result = check_portfolio(
            portfolio(
                security("A", 90_000),
                security("B", 90_000),
                security("C", 90_000),
                security("D", 90_000),
                security("E", 40_000),  # 4%, below the 5% trigger
            )
        )
        f = finding(result, "UCITS.ART52.2.AGGREGATE_40")
        self.assertAlmostEqual(f.actual, 36.0)
        self.assertEqual(f.severity, Severity.WARNING)

    def test_forty_percent_aggregate_can_breach(self) -> None:
        result = check_portfolio(portfolio(*[security(chr(65 + i), 90_000) for i in range(5)]))
        f = finding(result, "UCITS.ART52.2.AGGREGATE_40")
        self.assertAlmostEqual(f.actual, 45.0)
        self.assertEqual(f.severity, Severity.BREACH)

    def test_public_bodies_use_the_thirty_five_percent_limit_and_leave_the_forty(self) -> None:
        result = check_portfolio(
            portfolio(
                security("DE", 300_000, AssetClass.GOVERNMENT_BOND, is_public_body=True),
                security("A", 90_000),
            )
        )
        public = finding(result, "UCITS.ART52.3.PUBLIC_BODY", "DE")
        self.assertEqual(public.severity, Severity.PASS)
        self.assertEqual(public.limit, 35.0)
        self.assertAlmostEqual(finding(result, "UCITS.ART52.2.AGGREGATE_40").actual, 9.0)

    def test_public_body_derogation_lifts_the_limit(self) -> None:
        positions = portfolio(
            security("DE", 500_000, AssetClass.GOVERNMENT_BOND, is_public_body=True)
        )
        strict = finding(check_portfolio(positions), "UCITS.ART52.3.PUBLIC_BODY", "DE")
        relaxed = finding(
            check_portfolio(positions, UcitsConfig(art52_4_derogation=True)),
            "UCITS.ART52.3.PUBLIC_BODY",
            "DE",
        )
        self.assertEqual(strict.severity, Severity.BREACH)
        self.assertEqual(relaxed.severity, Severity.PASS)

    def test_covered_bonds_use_their_own_limits(self) -> None:
        result = check_portfolio(
            portfolio(
                security("NORD", 240_000, AssetClass.COVERED_BOND, is_covered_bond=True),
                security("DANS", 200_000, AssetClass.COVERED_BOND, is_covered_bond=True),
            )
        )
        self.assertEqual(finding(result, "UCITS.ART52.4.COVERED_BOND", "NORD").limit, 25.0)
        aggregate = finding(result, "UCITS.ART52.4.COVERED_BOND_AGGREGATE")
        self.assertAlmostEqual(aggregate.actual, 44.0)
        self.assertEqual(aggregate.severity, Severity.PASS)


class TestDepositsAndCounterparty(unittest.TestCase):
    def test_deposit_limit(self) -> None:
        result = check_portfolio(portfolio(security("BNP", 250_000, AssetClass.DEPOSIT)))
        f = finding(result, "UCITS.ART52.1B.DEPOSIT", "BNP")
        self.assertEqual(f.severity, Severity.BREACH)
        self.assertEqual(f.limit, 20.0)

    def test_collateral_reduces_counterparty_exposure(self) -> None:
        book = portfolio(
            otc(
                "swap",
                notional=200_000,
                mtm=50_000,
                underlying="IDX",
                counterparty="BNP",
                collateral=20_000,
            )
        )
        self.assertAlmostEqual(counterparty_exposures(book)["BNP"], 30_000)

    def test_netting_offsets_within_an_agreement(self) -> None:
        book = portfolio(
            otc(
                "a",
                notional=100_000,
                mtm=50_000,
                underlying="X",
                counterparty="BNP",
                netting_set="NS",
            ),
            otc(
                "b",
                notional=-100_000,
                mtm=-30_000,
                underlying="Y",
                counterparty="BNP",
                netting_set="NS",
            ),
        )
        self.assertAlmostEqual(counterparty_exposures(book)["BNP"], 20_000)

    def test_without_a_netting_set_a_negative_contract_does_not_offset(self) -> None:
        book = portfolio(
            otc("a", notional=100_000, mtm=50_000, underlying="X", counterparty="BNP"),
            otc("b", notional=-100_000, mtm=-30_000, underlying="Y", counterparty="BNP"),
        )
        self.assertAlmostEqual(counterparty_exposures(book)["BNP"], 50_000)

    def test_non_credit_institutions_get_the_five_percent_limit(self) -> None:
        book = portfolio(
            otc("a", notional=100_000, mtm=70_000, underlying="X", counterparty="HEDGEFUND")
        )
        config = UcitsConfig(credit_institutions={"BNP"})
        f = finding(check_portfolio(book, config), "UCITS.ART52.1.OTC_COUNTERPARTY", "HEDGEFUND")
        self.assertEqual(f.limit, 5.0)
        self.assertEqual(f.severity, Severity.BREACH)

    def test_combined_limit_adds_securities_deposits_and_otc(self) -> None:
        book = portfolio(
            security("BNP", 120_000, AssetClass.CORPORATE_BOND),
            security("BNP", 60_000, AssetClass.DEPOSIT),
            otc(
                "swap",
                notional=50_000,
                mtm=40_000,
                underlying="X",
                counterparty="BNP",
                issuer="OTHER",
                look_through=False,
            ),
        )
        f = finding(check_portfolio(book), "UCITS.ART52.2.COMBINED_20", "BNP")
        self.assertAlmostEqual(f.actual, 22.0)
        self.assertEqual(f.severity, Severity.BREACH)


class TestCollectiveInvestments(unittest.TestCase):
    def test_single_fund_limit(self) -> None:
        result = check_portfolio(portfolio(security("F1", 250_000, AssetClass.UCITS_FUND)))
        self.assertEqual(
            finding(result, "UCITS.ART55.1.SINGLE_CIS", "F1").severity, Severity.BREACH
        )

    def test_non_ucits_aggregate(self) -> None:
        result = check_portfolio(
            portfolio(
                security("A1", 180_000, AssetClass.NON_UCITS_CIS),
                security("A2", 160_000, AssetClass.NON_UCITS_CIS),
            )
        )
        f = finding(result, "UCITS.ART55.1.NON_UCITS_AGGREGATE")
        self.assertAlmostEqual(f.actual, 34.0)
        self.assertEqual(f.severity, Severity.BREACH)


class TestEligibilityAndBorrowing(unittest.TestCase):
    def test_trash_ratio(self) -> None:
        result = check_portfolio(portfolio(security("X", 120_000, AssetClass.INELIGIBLE)))
        f = finding(result, "UCITS.ART50.2A.INELIGIBLE")
        self.assertAlmostEqual(f.actual, 12.0)
        self.assertEqual(f.severity, Severity.BREACH)

    def test_a_security_flagged_outside_article_50_counts_as_ineligible(self) -> None:
        result = check_portfolio(
            portfolio(security("X", 120_000, AssetClass.EQUITY, is_eligible_art50=False))
        )
        self.assertEqual(finding(result, "UCITS.ART50.2A.INELIGIBLE").severity, Severity.BREACH)

    def test_borrowing_limit(self) -> None:
        result = check_portfolio(portfolio(security("A", 10_000), borrowing=150_000))
        self.assertEqual(finding(result, "UCITS.ART83.BORROWING").severity, Severity.BREACH)


class TestInfluenceLimits(unittest.TestCase):
    def test_holding_more_than_a_tenth_of_an_issue_breaches(self) -> None:
        instrument = Instrument(
            instrument_id="EQ",
            name="EQ",
            asset_class=AssetClass.EQUITY,
            issuer_id="ACME",
            issuer_total_units=1_000,
        )
        book = portfolio(Position(instrument=instrument, quantity=150, market_value=10_000))
        f = finding(check_portfolio(book), "UCITS.ART56.INFLUENCE", "EQ")
        self.assertEqual(f.severity, Severity.BREACH)
        self.assertAlmostEqual(f.actual, 15.0)
        self.assertEqual(f.unit, "% of issue")

    def test_missing_issue_size_is_inconclusive_rather_than_a_pass(self) -> None:
        book = portfolio(security("ACME", 10_000))
        f = finding(check_portfolio(book), "UCITS.ART56.INFLUENCE", "ACME")
        self.assertEqual(f.severity, Severity.INCONCLUSIVE)
        self.assertIsNone(f.actual)

    def test_funds_use_the_twenty_five_percent_limit(self) -> None:
        instrument = Instrument(
            instrument_id="FUND",
            name="FUND",
            asset_class=AssetClass.UCITS_FUND,
            issuer_id="FUND",
            issuer_total_units=1_000,
        )
        book = portfolio(Position(instrument=instrument, quantity=200, market_value=10_000))
        f = finding(check_portfolio(book), "UCITS.ART56.INFLUENCE", "FUND")
        self.assertEqual(f.limit, 25.0)
        self.assertEqual(f.severity, Severity.PASS)


class TestCommitmentApproach(unittest.TestCase):
    def test_longs_and_shorts_on_the_same_underlying_net_within_a_set(self) -> None:
        book = portfolio(
            otc("long", notional=300_000, mtm=0, underlying="SX5E", netting_set="NS"),
            otc("short", notional=-100_000, mtm=0, underlying="SX5E", netting_set="NS"),
        )
        result = commitment_approach_exposure(book)
        self.assertAlmostEqual(result.net_commitment, 200_000)
        self.assertAlmostEqual(result.gross_commitment, 400_000)
        self.assertAlmostEqual(result.global_exposure_pct, 20.0)
        self.assertAlmostEqual(result.netting_benefit_pct, 20.0)
        self.assertAlmostEqual(result.total_exposure_pct, 120.0)

    def test_different_underlyings_do_not_net(self) -> None:
        book = portfolio(
            otc("a", notional=300_000, mtm=0, underlying="SX5E", netting_set="NS"),
            otc("b", notional=-100_000, mtm=0, underlying="SPX", netting_set="NS"),
        )
        self.assertAlmostEqual(commitment_approach_exposure(book).net_commitment, 400_000)

    def test_netting_can_be_disabled_for_a_control_total(self) -> None:
        book = portfolio(
            otc("long", notional=300_000, mtm=0, underlying="SX5E", netting_set="NS"),
            otc("short", notional=-100_000, mtm=0, underlying="SX5E", netting_set="NS"),
        )
        gross = commitment_approach_exposure(book, allow_netting=False)
        self.assertAlmostEqual(gross.net_commitment, 400_000)

    def test_global_exposure_over_one_hundred_percent_breaches(self) -> None:
        book = portfolio(otc("big", notional=1_500_000, mtm=0, underlying="SX5E"))
        f = finding(check_portfolio(book), "UCITS.ART51.3.GLOBAL_EXPOSURE")
        self.assertEqual(f.severity, Severity.BREACH)
        self.assertAlmostEqual(f.actual, 150.0)

    def test_zero_nav_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            commitment_approach_exposure(Portfolio(fund_name="x", nav=0.0))


class TestIssuerLookThrough(unittest.TestCase):
    def test_single_name_derivatives_add_to_issuer_exposure(self) -> None:
        book = portfolio(
            security("ACME", 60_000),
            otc("ssf", notional=60_000, mtm=0, underlying="ACME", issuer="ACME"),
        )
        self.assertAlmostEqual(
            finding(check_portfolio(book), "UCITS.ART52.1A.SINGLE_ISSUER", "ACME").actual, 12.0
        )

    def test_index_derivatives_are_not_attributed_to_a_single_issuer(self) -> None:
        book = portfolio(
            security("ACME", 60_000),
            otc(
                "idx", notional=600_000, mtm=0, underlying="SX5E", issuer="SX5E", look_through=False
            ),
        )
        result = check_portfolio(book)
        self.assertAlmostEqual(finding(result, "UCITS.ART52.1A.SINGLE_ISSUER", "ACME").actual, 6.0)
        with self.assertRaises(AssertionError):
            finding(result, "UCITS.ART52.1A.SINGLE_ISSUER", "SX5E")


class TestSamplePortfolio(unittest.TestCase):
    """Regression test over the committed sample, so the published numbers hold."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.portfolio = load_portfolio(DATA / "sample_portfolio.csv")
        cls.result = check_portfolio(cls.portfolio)

    def test_loads_every_position(self) -> None:
        self.assertEqual(len(self.portfolio), 23)
        self.assertEqual(self.portfolio.nav, 100_000_000)

    def test_two_breaches_and_two_warnings(self) -> None:
        counts = self.result.counts()
        self.assertEqual(counts["breach"], 2)
        self.assertEqual(counts["warning"], 2)
        self.assertFalse(self.result.compliant)

    def test_acme_exceeds_the_issuer_limit_after_look_through(self) -> None:
        f = finding(self.result, "UCITS.ART52.1A.SINGLE_ISSUER", "ACME")
        self.assertAlmostEqual(f.actual, 12.5)
        self.assertEqual(f.severity, Severity.BREACH)

    def test_global_exposure_reflects_the_hedge(self) -> None:
        exposure = commitment_approach_exposure(self.portfolio)
        self.assertAlmostEqual(exposure.global_exposure_pct, 27.0)
        self.assertAlmostEqual(exposure.gross_exposure_pct, 33.0)

    def test_counterparty_exposure_nets_and_deducts_collateral(self) -> None:
        exposures = counterparty_exposures(self.portfolio)
        self.assertAlmostEqual(exposures["BNP"], 280_000)
        self.assertAlmostEqual(exposures["DEUTSCHE"], 250_000)


if __name__ == "__main__":
    unittest.main()
