"""When an AIFM must file, and for which period.

Article 110 of Commission Delegated Regulation (EU) 231/2013, which sets the
reporting frequency under Article 24 of Directive 2011/61/EU.

The thresholds interact, which is what makes this worth coding rather than
remembering: a mid-sized AIFM files half-yearly for most of its funds but
quarterly for any single AIF above EUR 500 million, and a sub-threshold AIFM
registered under Art. 3(2) files annually whatever its funds look like.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: Art. 3(2)(a) of Directive 2011/61/EU: the registration threshold for an AIFM
#: whose AIFs employ leverage.
THRESHOLD_LEVERAGED = 100_000_000.0
#: Art. 3(2)(b): the threshold for unleveraged AIFs with no redemption rights
#: exercisable for five years from initial investment.
THRESHOLD_UNLEVERAGED = 500_000_000.0
#: Art. 110(3): above this an AIFM reports quarterly.
THRESHOLD_QUARTERLY_AIFM = 1_000_000_000.0
#: Art. 110(3) second subparagraph: an individual AIF above this is reported
#: quarterly even where the AIFM itself is below the quarterly threshold.
THRESHOLD_QUARTERLY_AIF = 500_000_000.0


class Frequency(str, Enum):
    ANNUAL = "annual"
    HALF_YEARLY = "half-yearly"
    QUARTERLY = "quarterly"


class ReportingPeriodType(str, Enum):
    """The period codes an Annex IV filing carries."""

    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"
    H1 = "H1"
    H2 = "H2"
    Y1 = "Y1"


#: Period codes available under each frequency, in calendar order.
PERIODS: dict[Frequency, tuple[ReportingPeriodType, ...]] = {
    Frequency.QUARTERLY: (
        ReportingPeriodType.Q1,
        ReportingPeriodType.Q2,
        ReportingPeriodType.Q3,
        ReportingPeriodType.Q4,
    ),
    Frequency.HALF_YEARLY: (ReportingPeriodType.H1, ReportingPeriodType.H2),
    Frequency.ANNUAL: (ReportingPeriodType.Y1,),
}

#: Last calendar day of each reporting period, as (month, day).
_PERIOD_END = {
    ReportingPeriodType.Q1: (3, 31),
    ReportingPeriodType.Q2: (6, 30),
    ReportingPeriodType.Q3: (9, 30),
    ReportingPeriodType.Q4: (12, 31),
    ReportingPeriodType.H1: (6, 30),
    ReportingPeriodType.H2: (12, 31),
    ReportingPeriodType.Y1: (12, 31),
}

_PERIOD_START = {
    ReportingPeriodType.Q1: (1, 1),
    ReportingPeriodType.Q2: (4, 1),
    ReportingPeriodType.Q3: (7, 1),
    ReportingPeriodType.Q4: (10, 1),
    ReportingPeriodType.H1: (1, 1),
    ReportingPeriodType.H2: (7, 1),
    ReportingPeriodType.Y1: (1, 1),
}


@dataclass(frozen=True)
class ReportingObligation:
    """The filing frequency for one AIF, with the reason for it."""

    frequency: Frequency
    reference: str
    rationale: str
    aifm_aum: float
    aif_aum: float

    @property
    def periods(self) -> tuple[ReportingPeriodType, ...]:
        return PERIODS[self.frequency]

    def to_dict(self) -> dict:
        return {
            "frequency": self.frequency.value,
            "reference": self.reference,
            "rationale": self.rationale,
            "aifm_aum": self.aifm_aum,
            "aif_aum": self.aif_aum,
            "periods": [p.value for p in self.periods],
        }


def determine_frequency(
    *,
    aifm_aum: float,
    aif_aum: float,
    aifm_is_authorised: bool = True,
    aif_is_unleveraged_control_fund: bool = False,
) -> ReportingObligation:
    """Work out how often one AIF must be reported.

    Parameters
    ----------
    aifm_aum:
        Total assets under management across every AIF the AIFM manages.
    aif_aum:
        Assets under management of the individual AIF being reported.
    aifm_is_authorised:
        ``False`` for a sub-threshold AIFM merely registered under Art. 3(2),
        which reports annually regardless of what its funds hold.
    aif_is_unleveraged_control_fund:
        ``True`` for an unleveraged AIF investing in non-listed companies and
        issuers in order to acquire control, which Art. 110(4) places on an
        annual cycle.
    """

    if aifm_aum < 0 or aif_aum < 0:
        raise ValueError("assets under management cannot be negative")
    if aif_aum > aifm_aum:
        raise ValueError("a single AIF cannot hold more than the AIFM manages in total")

    if not aifm_is_authorised:
        return ReportingObligation(
            frequency=Frequency.ANNUAL,
            reference="Directive 2011/61/EU Art. 3(3)(d)",
            rationale=(
                "The AIFM is registered rather than authorised, so it reports annually "
                "under the sub-threshold regime."
            ),
            aifm_aum=aifm_aum,
            aif_aum=aif_aum,
        )

    if aif_is_unleveraged_control_fund:
        return ReportingObligation(
            frequency=Frequency.ANNUAL,
            reference="Regulation (EU) 231/2013 Art. 110(4)",
            rationale=(
                "The AIF is unleveraged and invests in non-listed companies and issuers "
                "in order to acquire control, which is reported annually."
            ),
            aifm_aum=aifm_aum,
            aif_aum=aif_aum,
        )

    if aifm_aum > THRESHOLD_QUARTERLY_AIFM:
        return ReportingObligation(
            frequency=Frequency.QUARTERLY,
            reference="Regulation (EU) 231/2013 Art. 110(3)",
            rationale=(
                f"The AIFM manages EUR {aifm_aum:,.0f}, above the EUR "
                f"{THRESHOLD_QUARTERLY_AIFM:,.0f} quarterly threshold."
            ),
            aifm_aum=aifm_aum,
            aif_aum=aif_aum,
        )

    if aif_aum > THRESHOLD_QUARTERLY_AIF:
        return ReportingObligation(
            frequency=Frequency.QUARTERLY,
            reference="Regulation (EU) 231/2013 Art. 110(3), second subparagraph",
            rationale=(
                f"The AIFM is below the quarterly threshold, but this AIF holds EUR "
                f"{aif_aum:,.0f}, above the EUR {THRESHOLD_QUARTERLY_AIF:,.0f} level at "
                "which an individual fund is reported quarterly."
            ),
            aifm_aum=aifm_aum,
            aif_aum=aif_aum,
        )

    if aifm_aum > THRESHOLD_LEVERAGED:
        return ReportingObligation(
            frequency=Frequency.HALF_YEARLY,
            reference="Regulation (EU) 231/2013 Art. 110(2)",
            rationale=(
                f"The AIFM manages EUR {aifm_aum:,.0f}, above the Art. 3(2) registration "
                f"threshold but at or below EUR {THRESHOLD_QUARTERLY_AIFM:,.0f}."
            ),
            aifm_aum=aifm_aum,
            aif_aum=aif_aum,
        )

    return ReportingObligation(
        frequency=Frequency.ANNUAL,
        reference="Regulation (EU) 231/2013 Art. 110(1)",
        rationale=(f"The AIFM manages EUR {aifm_aum:,.0f}, at or below the Art. 3(2) threshold."),
        aifm_aum=aifm_aum,
        aif_aum=aif_aum,
    )


def period_for_date(frequency: Frequency, valuation_date: str) -> ReportingPeriodType:
    """The reporting period an ISO ``YYYY-MM-DD`` valuation date falls in."""

    from datetime import date

    day = date.fromisoformat(valuation_date)
    for period in PERIODS[frequency]:
        end_month, end_day = _PERIOD_END[period]
        if (day.month, day.day) <= (end_month, end_day):
            return period
    return PERIODS[frequency][-1]


def period_bounds(period: ReportingPeriodType, year: int) -> tuple[str, str]:
    """Start and end dates of a reporting period as ISO strings."""

    start_month, start_day = _PERIOD_START[period]
    end_month, end_day = _PERIOD_END[period]
    return (
        f"{year:04d}-{start_month:02d}-{start_day:02d}",
        f"{year:04d}-{end_month:02d}-{end_day:02d}",
    )
