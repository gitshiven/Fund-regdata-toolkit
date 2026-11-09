"""Tests for AIFMD leverage, reporting frequency, Annex IV assembly and XML."""

from __future__ import annotations

import unittest
from pathlib import Path
from xml.etree import ElementTree

from fundreg.aifmd import (
    SUBSTANTIAL_LEVERAGE_RATIO,
    AifInfo,
    AifmInfo,
    Frequency,
    LeverageConfig,
    ReportingPeriodType,
    assets_under_management,
    build_report,
    commitment_method_exposure,
    determine_frequency,
    gross_method_exposure,
    leverage_profile,
    load_annex_iv_spec,
    period_bounds,
    period_for_date,
    principal_exposures,
    report_to_xml,
    to_xml,
)
from fundreg.findings import Severity
from fundreg.models import (
    AssetClass,
    Derivative,
    Instrument,
    Portfolio,
    Position,
    load_portfolio,
)
from fundreg.templates import Requirement
from fundreg.templates.validator import validate_records

DATA = Path(__file__).resolve().parents[1] / "data"


def security(issuer: str, value: float, asset_class: AssetClass = AssetClass.EQUITY) -> Position:
    return Position(
        instrument=Instrument(
            instrument_id=f"{issuer}_{asset_class.value}",
            name=issuer,
            asset_class=asset_class,
            issuer_id=issuer,
            issuer_name=issuer,
        ),
        quantity=1.0,
        market_value=value,
    )


def otc(
    name: str,
    *,
    notional: float,
    mtm: float,
    underlying: str,
    netting_set: str | None = None,
) -> Position:
    return Position(
        instrument=Instrument(
            instrument_id=name,
            name=name,
            asset_class=AssetClass.OTC_DERIVATIVE,
            issuer_id=underlying,
            look_through_to_issuer=False,
            derivative=Derivative(
                underlying_id=underlying, notional=notional, netting_set=netting_set
            ),
        ),
        quantity=1.0,
        market_value=mtm,
    )


def portfolio(*positions: Position, nav: float = 1_000_000.0, borrowing: float = 0.0) -> Portfolio:
    return Portfolio(
        fund_name="Test Fund",
        nav=nav,
        positions=list(positions),
        borrowing=borrowing,
        valuation_date="2026-09-30",
    )


class TestGrossMethod(unittest.TestCase):
    def test_unleveraged_portfolio_is_one_times_nav(self) -> None:
        book = portfolio(security("A", 600_000), security("B", 400_000))
        result = gross_method_exposure(book)
        self.assertAlmostEqual(result.ratio, 1.0)
        self.assertAlmostEqual(result.exposure, 1_000_000)

    def test_cash_and_deposits_are_excluded(self) -> None:
        book = portfolio(
            security("A", 600_000),
            security("CASH", 300_000, AssetClass.CASH),
            security("BNP", 100_000, AssetClass.DEPOSIT),
        )
        self.assertAlmostEqual(gross_method_exposure(book).exposure, 600_000)

    def test_cash_can_be_retained_by_configuration(self) -> None:
        book = portfolio(security("A", 600_000), security("CASH", 300_000, AssetClass.CASH))
        config = LeverageConfig(exclude_cash_equivalents=False)
        self.assertAlmostEqual(gross_method_exposure(book, config).exposure, 900_000)

    def test_longs_and_shorts_both_add_exposure(self) -> None:
        book = portfolio(
            otc("long", notional=300_000, mtm=0, underlying="X", netting_set="NS"),
            otc("short", notional=-300_000, mtm=0, underlying="X", netting_set="NS"),
        )
        self.assertAlmostEqual(gross_method_exposure(book).exposure, 600_000)

    def test_reinvested_borrowing_adds_exposure(self) -> None:
        book = portfolio(security("A", 1_000_000), borrowing=100_000)
        self.assertAlmostEqual(gross_method_exposure(book).exposure, 1_100_000)

    def test_borrowing_held_in_cash_is_excluded(self) -> None:
        book = portfolio(security("A", 1_000_000), borrowing=100_000)
        config = LeverageConfig(borrowing_reinvested=False)
        result = gross_method_exposure(book, config)
        self.assertAlmostEqual(result.exposure, 1_000_000)
        self.assertTrue(any("held in cash" in c.label for c in result.excluded))

    def test_zero_nav_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            gross_method_exposure(Portfolio(fund_name="x", nav=0.0))


class TestCommitmentMethod(unittest.TestCase):
    def test_netting_reduces_exposure_against_the_gross_method(self) -> None:
        book = portfolio(
            security("A", 500_000),
            otc("long", notional=300_000, mtm=0, underlying="X", netting_set="NS"),
            otc("short", notional=-100_000, mtm=0, underlying="X", netting_set="NS"),
        )
        gross = gross_method_exposure(book)
        commitment = commitment_method_exposure(book)
        self.assertAlmostEqual(gross.exposure, 900_000)
        self.assertAlmostEqual(commitment.exposure, 700_000)
        self.assertGreater(gross.ratio, commitment.ratio)

    def test_commitment_never_exceeds_gross(self) -> None:
        book = load_portfolio(DATA / "sample_portfolio.csv")
        profile = leverage_profile(book)
        self.assertGreaterEqual(profile.gross.ratio, profile.commitment.ratio)

    def test_substantial_leverage_threshold(self) -> None:
        book = portfolio(
            security("A", 1_000_000),
            otc("big", notional=2_500_000, mtm=0, underlying="X"),
        )
        result = commitment_method_exposure(book)
        self.assertAlmostEqual(result.ratio, 3.5)
        self.assertTrue(result.substantially_leveraged)

    def test_exactly_three_times_is_not_substantial(self) -> None:
        book = portfolio(
            security("A", 1_000_000),
            otc("big", notional=2_000_000, mtm=0, underlying="X"),
        )
        result = commitment_method_exposure(book)
        self.assertAlmostEqual(result.ratio, SUBSTANTIAL_LEVERAGE_RATIO)
        self.assertFalse(result.substantially_leveraged)


class TestComparisonWithUcits(unittest.TestCase):
    def test_aifmd_and_ucits_measure_different_things(self) -> None:
        """The headline point: these two numbers are not comparable."""
        book = load_portfolio(DATA / "sample_portfolio.csv")
        profile = leverage_profile(book)
        # UCITS counts only the incremental derivative exposure.
        self.assertAlmostEqual(profile.ucits_global_exposure_pct, 27.0)
        # AIFMD counts the whole book, so the same fund is above 1.0x.
        self.assertAlmostEqual(profile.commitment.ratio, 1.255, places=3)
        self.assertAlmostEqual(profile.gross.ratio, 1.315, places=3)


class TestAssetsUnderManagement(unittest.TestCase):
    def test_aum_includes_cash_unlike_the_leverage_measures(self) -> None:
        book = portfolio(
            security("A", 600_000),
            security("CASH", 300_000, AssetClass.CASH),
        )
        self.assertAlmostEqual(assets_under_management(book), 900_000)
        self.assertAlmostEqual(gross_method_exposure(book).exposure, 600_000)

    def test_derivatives_count_at_their_underlying_exposure(self) -> None:
        book = portfolio(otc("d", notional=-400_000, mtm=10_000, underlying="X"))
        self.assertAlmostEqual(assets_under_management(book), 400_000)

    def test_sample_portfolio(self) -> None:
        book = load_portfolio(DATA / "sample_portfolio.csv")
        self.assertAlmostEqual(assets_under_management(book), 132_370_000)


class TestPrincipalExposures(unittest.TestCase):
    def test_ranked_largest_first_with_look_through(self) -> None:
        book = load_portfolio(DATA / "sample_portfolio.csv")
        exposures = principal_exposures(book)
        self.assertEqual(len(exposures), 3)
        values = [v for _, v in exposures]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertEqual(exposures[0][0], "Federal Republic of Germany")
        # Acme is second only once the single-stock future is looked through.
        self.assertEqual(exposures[1][0], "Acme Industrial SA")
        self.assertAlmostEqual(exposures[1][1], 12_500_000)

    def test_limit_is_respected(self) -> None:
        book = load_portfolio(DATA / "sample_portfolio.csv")
        self.assertEqual(len(principal_exposures(book, limit=1)), 1)


class TestReportingFrequency(unittest.TestCase):
    def test_large_aifm_reports_quarterly(self) -> None:
        obligation = determine_frequency(aifm_aum=4_200_000_000, aif_aum=100_000_000)
        self.assertEqual(obligation.frequency, Frequency.QUARTERLY)
        self.assertIn("110(3)", obligation.reference)

    def test_mid_sized_aifm_reports_half_yearly(self) -> None:
        obligation = determine_frequency(aifm_aum=400_000_000, aif_aum=100_000_000)
        self.assertEqual(obligation.frequency, Frequency.HALF_YEARLY)

    def test_a_large_single_aif_pulls_a_mid_sized_aifm_to_quarterly(self) -> None:
        obligation = determine_frequency(aifm_aum=800_000_000, aif_aum=600_000_000)
        self.assertEqual(obligation.frequency, Frequency.QUARTERLY)
        self.assertIn("second subparagraph", obligation.reference)

    def test_small_aifm_reports_annually(self) -> None:
        obligation = determine_frequency(aifm_aum=80_000_000, aif_aum=50_000_000)
        self.assertEqual(obligation.frequency, Frequency.ANNUAL)

    def test_sub_threshold_registration_overrides_size(self) -> None:
        obligation = determine_frequency(
            aifm_aum=5_000_000_000, aif_aum=100_000_000, aifm_is_authorised=False
        )
        self.assertEqual(obligation.frequency, Frequency.ANNUAL)
        self.assertIn("3(3)(d)", obligation.reference)

    def test_unleveraged_control_fund_reports_annually(self) -> None:
        obligation = determine_frequency(
            aifm_aum=5_000_000_000,
            aif_aum=100_000_000,
            aif_is_unleveraged_control_fund=True,
        )
        self.assertEqual(obligation.frequency, Frequency.ANNUAL)
        self.assertIn("110(4)", obligation.reference)

    def test_periods_available_per_frequency(self) -> None:
        quarterly = determine_frequency(aifm_aum=4_000_000_000, aif_aum=1_000_000)
        self.assertEqual(len(quarterly.periods), 4)
        annual = determine_frequency(aifm_aum=10_000_000, aif_aum=1_000_000)
        self.assertEqual(annual.periods, (ReportingPeriodType.Y1,))

    def test_impossible_inputs_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            determine_frequency(aifm_aum=-1, aif_aum=0)
        with self.assertRaises(ValueError):
            determine_frequency(aifm_aum=100, aif_aum=200)


class TestPeriodMath(unittest.TestCase):
    def test_valuation_date_maps_to_the_right_quarter(self) -> None:
        cases = [
            ("2026-01-01", ReportingPeriodType.Q1),
            ("2026-03-31", ReportingPeriodType.Q1),
            ("2026-04-01", ReportingPeriodType.Q2),
            ("2026-09-30", ReportingPeriodType.Q3),
            ("2026-12-31", ReportingPeriodType.Q4),
        ]
        for valuation, expected in cases:
            self.assertEqual(
                period_for_date(Frequency.QUARTERLY, valuation), expected, msg=valuation
            )

    def test_half_years(self) -> None:
        self.assertEqual(
            period_for_date(Frequency.HALF_YEARLY, "2026-06-30"), ReportingPeriodType.H1
        )
        self.assertEqual(
            period_for_date(Frequency.HALF_YEARLY, "2026-07-01"), ReportingPeriodType.H2
        )

    def test_bounds(self) -> None:
        self.assertEqual(period_bounds(ReportingPeriodType.Q3, 2026), ("2026-07-01", "2026-09-30"))
        self.assertEqual(period_bounds(ReportingPeriodType.Y1, 2026), ("2026-01-01", "2026-12-31"))


class TestAnnexIVSpec(unittest.TestCase):
    def test_spec_loads_and_is_internally_consistent(self) -> None:
        from fundreg.templates.validator import _fields_in

        spec = load_annex_iv_spec()
        known = set(spec.by_code)
        self.assertIn(spec.key_field, known)
        for rule in spec.rules:
            used = _fields_in(rule.when) | _fields_in(rule.then)
            self.assertTrue(used <= known, msg=f"{rule.rule_id}: {used - known}")
        for declared in spec.fields:
            if declared.requirement is Requirement.CONDITIONAL:
                self.assertIsNotNone(declared.required_when, msg=declared.code)

    def test_period_end_before_start_is_caught(self) -> None:
        record = {
            "ReportingPeriodStartDate": "2026-10-01",
            "ReportingPeriodEndDate": "2026-09-30",
        }
        result = validate_records([record], load_annex_iv_spec())
        self.assertTrue(
            any(f.rule_id == "ANNEXIV.PERIOD.END_AFTER_START" for f in result.findings.breaches)
        )

    def test_gross_below_commitment_is_caught(self) -> None:
        record = {
            "LeverageGrossMethodRatio": "1.10",
            "LeverageCommitmentMethodRatio": "1.40",
        }
        result = validate_records([record], load_annex_iv_spec())
        self.assertTrue(
            any(
                f.rule_id == "ANNEXIV.LEVERAGE.GROSS_AT_LEAST_COMMITMENT"
                for f in result.findings.breaches
            )
        )

    def test_unflagged_substantial_leverage_is_caught(self) -> None:
        record = {
            "LeverageCommitmentMethodRatio": "4.2",
            "SubstantialLeverageFlag": "false",
        }
        result = validate_records([record], load_annex_iv_spec())
        self.assertTrue(
            any(
                f.rule_id == "ANNEXIV.LEVERAGE.SUBSTANTIAL_FLAG_SET"
                for f in result.findings.breaches
            )
        )

    def test_overstated_flag_warns(self) -> None:
        record = {
            "LeverageCommitmentMethodRatio": "1.2",
            "SubstantialLeverageFlag": "true",
        }
        result = validate_records([record], load_annex_iv_spec())
        self.assertTrue(
            any(
                f.rule_id == "ANNEXIV.LEVERAGE.SUBSTANTIAL_FLAG_NOT_OVERSTATED"
                and f.severity is Severity.WARNING
                for f in result.findings
            )
        )

    def test_eea_flag_must_match_domicile(self) -> None:
        record = {"AIFEEAFlag": "true", "AIFDomicile": "KY"}
        result = validate_records([record], load_annex_iv_spec())
        self.assertTrue(
            any(
                f.rule_id == "ANNEXIV.IDENTIFICATION.EEA_FLAG_MATCHES_DOMICILE"
                for f in result.findings.breaches
            )
        )
        good = validate_records([{"AIFEEAFlag": "true", "AIFDomicile": "LU"}], load_annex_iv_spec())
        self.assertFalse(
            any(
                f.rule_id == "ANNEXIV.IDENTIFICATION.EEA_FLAG_MATCHES_DOMICILE"
                for f in good.findings.breaches
            )
        )

    def test_feeder_requires_a_master_name(self) -> None:
        record = {"AIFMasterFeederStatus": "FEEDER", "MasterAIFName": ""}
        result = validate_records([record], load_annex_iv_spec())
        self.assertTrue(any(f.subject.endswith("MasterAIFName") for f in result.findings.breaches))

    def test_unranked_principal_exposures_are_caught(self) -> None:
        record = {"PrincipalExposure1_Value": "10", "PrincipalExposure2_Value": "20"}
        result = validate_records([record], load_annex_iv_spec())
        self.assertTrue(
            any(f.rule_id == "ANNEXIV.EXPOSURE.RANKED_IN_ORDER" for f in result.findings.breaches)
        )


class TestReportAssembly(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.portfolio = load_portfolio(DATA / "sample_portfolio.csv")
        cls.report = build_report(
            cls.portfolio,
            AifmInfo(
                name="Meridian Asset Management S.A.",
                national_code="LU-AIFM-0001",
                lei="5493001KJTIIGC8Y1R12",
                total_aum=4_200_000_000,
            ),
            AifInfo(
                name="Meridian European Equity Fund",
                national_code="LU-AIF-0001",
                inception_date="2016-03-01",
            ),
        )

    def test_record_validates_cleanly(self) -> None:
        self.assertTrue(self.report.valid, msg=self.report.findings.summary_line())

    def test_period_is_derived_from_the_valuation_date(self) -> None:
        self.assertEqual(self.report.record["ReportingPeriodType"], "Q3")
        self.assertEqual(self.report.record["ReportingPeriodYear"], "2026")
        self.assertEqual(self.report.record["ReportingPeriodStartDate"], "2026-07-01")
        self.assertEqual(self.report.record["ReportingPeriodEndDate"], "2026-09-30")

    def test_content_type_follows_the_leverage_result(self) -> None:
        self.assertFalse(self.report.substantially_leveraged)
        self.assertEqual(self.report.record["AIFContentType"], "3")
        self.assertEqual(self.report.record["SubstantialLeverageFlag"], "false")

    def test_a_substantially_leveraged_aif_files_content_type_four(self) -> None:
        book = portfolio(
            security("A", 1_000_000),
            otc("big", notional=3_000_000, mtm=0, underlying="X"),
        )
        book.fund_name = "Leveraged Fund"
        book.valuation_date = "2026-09-30"
        report = build_report(
            book,
            AifmInfo(name="M", national_code="A", total_aum=4_000_000_000),
            AifInfo(name="Leveraged Fund", national_code="LF"),
        )
        self.assertTrue(report.substantially_leveraged)
        self.assertEqual(report.record["AIFContentType"], "4")
        self.assertEqual(report.record["SubstantialLeverageFlag"], "true")
        self.assertTrue(report.valid, msg=report.findings.summary_line())

    def test_counterparty_is_reported_net_of_collateral(self) -> None:
        self.assertEqual(self.report.record["TopCounterpartyName"], "BNP")
        self.assertAlmostEqual(float(self.report.record["TopCounterpartyExposure"]), 280_000)

    def test_aum_and_nav_are_different_numbers(self) -> None:
        self.assertAlmostEqual(float(self.report.record["AIFNetAssetValue"]), 100_000_000)
        self.assertAlmostEqual(float(self.report.record["AIFAUMAmountInEuro"]), 132_370_000)

    def test_serialises_to_a_dictionary(self) -> None:
        import json

        json.dumps(self.report.to_dict())


class TestXmlSerialisation(unittest.TestCase):
    def setUp(self) -> None:
        self.report = build_report(
            load_portfolio(DATA / "sample_portfolio.csv"),
            AifmInfo(name="M", national_code="LU-AIFM-0001", total_aum=4_200_000_000),
            AifInfo(name="Fund", national_code="LU-AIF-0001"),
        )

    def test_document_is_well_formed_and_shaped_correctly(self) -> None:
        document = report_to_xml(self.report, created="2026-11-09T12:00:00")
        self.assertTrue(document.startswith('<?xml version="1.0" encoding="UTF-8"?>'))
        root = ElementTree.fromstring(document)
        self.assertEqual(root.tag, "AIFMReportingInfo")
        self.assertEqual(root.attrib["ReportingMemberState"], "LU")
        self.assertEqual(root.attrib["CreationDateAndTime"], "2026-11-09T12:00:00")
        records = root.findall("AIFRecordInfo")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].findtext("AIFName"), "Fund")
        self.assertEqual(records[0].findtext("ReportingPeriodType"), "Q3")

    def test_elements_follow_the_spec_order(self) -> None:
        root = ElementTree.fromstring(report_to_xml(self.report))
        emitted = [child.tag for child in root.find("AIFRecordInfo")]
        order = [f.code for f in load_annex_iv_spec().fields if f.code != "ReportingMemberState"]
        positions = [order.index(tag) for tag in emitted if tag in order]
        self.assertEqual(positions, sorted(positions))

    def test_empty_values_are_omitted(self) -> None:
        root = ElementTree.fromstring(report_to_xml(self.report))
        record = root.find("AIFRecordInfo")
        self.assertIsNone(record.find("MasterAIFName"))
        self.assertTrue(all((child.text or "").strip() for child in record))

    def test_reporting_member_state_is_on_the_envelope_only(self) -> None:
        root = ElementTree.fromstring(report_to_xml(self.report))
        self.assertIsNone(root.find("AIFRecordInfo").find("ReportingMemberState"))

    def test_several_records_in_one_file(self) -> None:
        document = to_xml(
            [self.report.record, dict(self.report.record, AIFNationalCode="LU-AIF-0002")],
            reporting_member_state="IE",
        )
        root = ElementTree.fromstring(document)
        self.assertEqual(len(root.findall("AIFRecordInfo")), 2)
        self.assertEqual(root.attrib["ReportingMemberState"], "IE")

    def test_undeclared_fields_are_still_written(self) -> None:
        document = to_xml([dict(self.report.record, CustomNationalField="X")])
        root = ElementTree.fromstring(document)
        self.assertEqual(root.find("AIFRecordInfo").findtext("CustomNationalField"), "X")

    def test_special_characters_are_escaped(self) -> None:
        document = to_xml([{"AIFName": "Fish & Chips <Fund>"}])
        self.assertIn("&amp;", document)
        root = ElementTree.fromstring(document)
        self.assertEqual(root.find("AIFRecordInfo").findtext("AIFName"), "Fish & Chips <Fund>")


if __name__ == "__main__":
    unittest.main()
