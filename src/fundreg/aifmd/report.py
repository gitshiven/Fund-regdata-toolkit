"""Assemble and validate an AIFMD Annex IV AIF-level record.

Most of the record is reference data the AIFM supplies: domicile, strategy,
master-feeder status. What the toolkit can derive from a portfolio is the part
that is actually hard, and the part a regulator will query:

* assets under management on the Art. 2 basis, which is not the net asset value;
* leverage under both Art. 7 and Art. 8;
* the three largest exposures after derivative look-through;
* the largest counterparty net of collateral;
* whether the AIF crosses the Art. 111 substantial leverage threshold, and
  therefore whether an Art. 24(4) filing is due.

The assembled record is a flat mapping of Annex IV element names to strings,
which means the existing template validator checks it before it is serialised.
A field problem is caught as a finding rather than as malformed XML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from fundreg.aifmd.frequency import (
    Frequency,
    ReportingObligation,
    ReportingPeriodType,
    determine_frequency,
    period_bounds,
    period_for_date,
)
from fundreg.aifmd.leverage import LeverageConfig, LeverageProfile, leverage_profile
from fundreg.findings import FindingSet
from fundreg.models import DERIVATIVE_CLASSES, Portfolio
from fundreg.templates.spec import TemplateSpec
from fundreg.templates.validator import validate_records

ANNEX_IV_AIF = "annex_iv_aif_v1_2"


def load_annex_iv_spec() -> TemplateSpec:
    return TemplateSpec.named(ANNEX_IV_AIF)


@dataclass
class AifmInfo:
    """The manager behind the filing."""

    name: str
    national_code: str
    reporting_member_state: str = "LU"
    lei: str = ""
    #: Total assets under management across every AIF managed, in euro. Drives
    #: the Art. 110 reporting frequency.
    total_aum: float = 0.0
    #: False for a sub-threshold AIFM registered under Art. 3(2).
    is_authorised: bool = True


@dataclass
class AifInfo:
    """Reference data about the fund that no portfolio file carries."""

    name: str
    national_code: str
    domicile: str = "LU"
    is_eea: bool = True
    inception_date: str = ""
    master_feeder_status: str = "NONE"
    master_aif_name: str = ""
    predominant_type: str = "OTHR"
    geographical_focus: str = "EEA"
    #: True for an unleveraged AIF acquiring control of non-listed companies,
    #: which Art. 110(4) puts on an annual cycle.
    is_unleveraged_control_fund: bool = False


@dataclass
class AnnexIVReport:
    """A computed Annex IV record with everything behind it."""

    aifm: AifmInfo
    aif: AifInfo
    record: dict[str, str]
    leverage: LeverageProfile
    obligation: ReportingObligation
    findings: FindingSet
    aum: float
    principal_exposures: list[tuple[str, float]] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.findings.compliant

    @property
    def substantially_leveraged(self) -> bool:
        return self.leverage.substantially_leveraged

    def to_dict(self) -> dict:
        return {
            "aifm": {
                "name": self.aifm.name,
                "national_code": self.aifm.national_code,
                "total_aum": self.aifm.total_aum,
            },
            "aif": {
                "name": self.aif.name,
                "national_code": self.aif.national_code,
                "domicile": self.aif.domicile,
                "predominant_type": self.aif.predominant_type,
            },
            "aum": round(self.aum, 2),
            "leverage": self.leverage.to_dict(),
            "obligation": self.obligation.to_dict(),
            "principal_exposures": [
                {"name": n, "value": round(v, 2)} for n, v in self.principal_exposures
            ],
            "counts": self.findings.counts(),
            "valid": self.valid,
            "record": self.record,
        }


def assets_under_management(portfolio: Portfolio, config: LeverageConfig | None = None) -> float:
    """Assets under management on the Art. 2 of Regulation (EU) 231/2013 basis.

    The sum of the absolute values of every asset, with derivatives converted
    into the equivalent position in their underlying. Unlike the leverage
    measures this does *not* exclude cash, because the question is how much the
    manager runs rather than how much exposure the fund carries.
    """

    config = config or LeverageConfig()
    total = 0.0
    for position in portfolio:
        if position.asset_class in DERIVATIVE_CLASSES:
            derivative = position.instrument.derivative
            assert derivative is not None
            total += abs(derivative.notional)
        else:
            total += abs(position.market_value)
    return total


def principal_exposures(portfolio: Portfolio, limit: int = 3) -> list[tuple[str, float]]:
    """The largest issuer exposures after derivative look-through.

    Reuses the UCITS issuer aggregation, so an index derivative is not
    misattributed to a single name and a single-stock future is.
    """

    from fundreg.ucits.limits import _issuer_security_exposure

    buckets = _issuer_security_exposure(portfolio)
    ranked = sorted(
        ((b["name"] or issuer, abs(b["value"])) for issuer, b in buckets.items()),
        key=lambda pair: -pair[1],
    )
    return ranked[:limit]


def _boolean(value: bool) -> str:
    return "true" if value else "false"


def build_report(
    portfolio: Portfolio,
    aifm: AifmInfo,
    aif: AifInfo,
    *,
    reporting_year: int | None = None,
    reporting_period: ReportingPeriodType | None = None,
    filing_type: str = "INIT",
    config: LeverageConfig | None = None,
) -> AnnexIVReport:
    """Compute a full Annex IV AIF record from a portfolio.

    The content type is chosen from the leverage result rather than supplied:
    an AIF over the Art. 111 threshold files the Art. 24(4) report, and one
    below it files under Art. 24(2).
    """

    config = config or LeverageConfig()
    profile = leverage_profile(portfolio, config)
    aum = assets_under_management(portfolio, config)

    obligation = determine_frequency(
        aifm_aum=max(aifm.total_aum, aum),
        aif_aum=aum,
        aifm_is_authorised=aifm.is_authorised,
        aif_is_unleveraged_control_fund=aif.is_unleveraged_control_fund,
    )

    valuation = portfolio.valuation_date or date.today().isoformat()
    year = reporting_year if reporting_year is not None else int(valuation[:4])
    period = reporting_period or period_for_date(obligation.frequency, valuation)
    start, end = period_bounds(period, year)

    exposures = principal_exposures(portfolio)

    from fundreg.ucits.exposure import counterparty_exposures

    counterparties = counterparty_exposures(portfolio)
    top_counterparty = max(counterparties.items(), key=lambda kv: kv[1], default=None)

    record: dict[str, str] = {
        "FilingType": filing_type,
        "AIFContentType": "4" if profile.substantially_leveraged else "3",
        "ReportingMemberState": aifm.reporting_member_state,
        "ReportingPeriodType": period.value,
        "ReportingPeriodYear": str(year),
        "ReportingPeriodStartDate": start,
        "ReportingPeriodEndDate": end,
        "AIFMNationalCode": aifm.national_code,
        "AIFMIdentifierLEI": aifm.lei,
        "AIFNationalCode": aif.national_code,
        "AIFName": aif.name,
        "AIFEEAFlag": _boolean(aif.is_eea),
        "AIFDomicile": aif.domicile,
        "AIFBaseCurrency": portfolio.base_currency,
        "InceptionDate": aif.inception_date,
        "AIFMasterFeederStatus": aif.master_feeder_status,
        "MasterAIFName": aif.master_aif_name,
        "PredominantAIFType": aif.predominant_type,
        "GeographicalFocus": aif.geographical_focus,
        "AIFNetAssetValue": f"{portfolio.nav:.2f}",
        "AIFAUMAmountInEuro": f"{aum:.2f}",
        "LeverageGrossMethodRatio": f"{profile.gross.ratio:.4f}",
        "LeverageCommitmentMethodRatio": f"{profile.commitment.ratio:.4f}",
        "SubstantialLeverageFlag": _boolean(profile.substantially_leveraged),
        "BorrowingTotal": f"{portfolio.borrowing:.2f}",
    }

    for index, (name, value) in enumerate(exposures, start=1):
        record[f"PrincipalExposure{index}_Name"] = name
        record[f"PrincipalExposure{index}_Value"] = f"{value:.2f}"

    if top_counterparty:
        record["TopCounterpartyName"] = top_counterparty[0]
        record["TopCounterpartyExposure"] = f"{top_counterparty[1]:.2f}"

    result = validate_records([record], load_annex_iv_spec(), source=aif.national_code)

    return AnnexIVReport(
        aifm=aifm,
        aif=aif,
        record=record,
        leverage=profile,
        obligation=obligation,
        findings=result.findings,
        aum=aum,
        principal_exposures=exposures,
    )


def frequency_label(frequency: Frequency) -> str:
    return frequency.value
