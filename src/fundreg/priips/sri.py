"""PRIIPs Summary Risk Indicator (SRI).

Implements Annex II of Commission Delegated Regulation (EU) 2017/653 as amended
by (EU) 2021/2268: the Market Risk Measure derived from the VaR-equivalent
volatility, the Credit Risk Measure derived from credit quality steps, and the
matrix that combines them into the 1-to-7 figure printed on the KID.

Most funds are Category 2 PRIIPs - they can be modelled from a representative
history of returns and do not offer a capital guarantee or an unusual payoff.
That is the case this module solves in full.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from fundreg.stats import Z_97_5, moments

#: Annex II point 2: MRM class boundaries on the VaR-equivalent volatility,
#: expressed as annualised decimals. Each entry is the exclusive upper bound.
MRM_BANDS: tuple[tuple[float, int], ...] = (
    (0.005, 1),
    (0.05, 2),
    (0.12, 3),
    (0.20, 4),
    (0.30, 5),
    (0.80, 6),
    (math.inf, 7),
)

#: Annex II Table 1: credit quality step to credit risk class, before the
#: maturity adjustment of point 45.
CQS_TO_CREDIT_CLASS: dict[int, int] = {0: 1, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6}

#: Annex II point 52: the SRI is read off this matrix, credit risk class down
#: the side and market risk class across the top.
SRI_MATRIX: dict[int, tuple[int, ...]] = {
    #     MR1 MR2 MR3 MR4 MR5 MR6 MR7
    1: (1, 2, 3, 4, 5, 6, 7),
    2: (1, 2, 3, 4, 5, 6, 7),
    3: (3, 3, 3, 4, 5, 6, 7),
    4: (5, 5, 5, 5, 5, 6, 7),
    5: (5, 5, 5, 5, 5, 6, 7),
    6: (6, 6, 6, 6, 6, 6, 7),
}

#: The narrative that accompanies each SRI figure on the KID.
SRI_NARRATIVE: dict[int, str] = {
    1: "the lowest risk class",
    2: "a low risk class",
    3: "a medium-low risk class",
    4: "a medium risk class",
    5: "a medium-high risk class",
    6: "the second-highest risk class",
    7: "the highest risk class",
}


@dataclass(frozen=True)
class MarketRiskResult:
    """Market Risk Measure for a Category 2 PRIIP."""

    #: Value at Risk at the 97.5% confidence level over the RHP, in log space.
    var_log: float
    #: VaR-equivalent volatility, annualised.
    vev: float
    mrm_class: int
    periods: float
    volatility: float
    skewness: float
    excess_kurtosis: float
    observations: int

    @property
    def var_return(self) -> float:
        """The 2.5% worst cumulative simple return implied by the VaR."""
        return math.exp(self.var_log) - 1.0


@dataclass(frozen=True)
class CreditRiskResult:
    """Credit Risk Measure."""

    crm_class: int
    credit_quality_step: int | None
    adjusted_credit_quality_step: int | None
    maturity_years: float | None
    rationale: str


@dataclass(frozen=True)
class SriResult:
    """Combined Summary Risk Indicator."""

    sri: int
    market: MarketRiskResult
    credit: CreditRiskResult

    @property
    def narrative(self) -> str:
        return (
            f"We have classified this product as {self.sri} out of 7, which is "
            f"{SRI_NARRATIVE[self.sri]}."
        )

    def to_dict(self) -> dict:
        return {
            "sri": self.sri,
            "mrm_class": self.market.mrm_class,
            "crm_class": self.credit.crm_class,
            "vev": round(self.market.vev, 6),
            "var_97_5_log": round(self.market.var_log, 6),
            "var_97_5_return": round(self.market.var_return, 6),
            "volatility_per_period": round(self.market.volatility, 8),
            "skewness": round(self.market.skewness, 6),
            "excess_kurtosis": round(self.market.excess_kurtosis, 6),
            "observations": self.market.observations,
            "credit_quality_step": self.credit.credit_quality_step,
            "credit_rationale": self.credit.rationale,
            "narrative": self.narrative,
        }


def mrm_class_from_vev(vev: float) -> int:
    """Map a VaR-equivalent volatility to an MRM class (Annex II point 2)."""

    if vev < 0:
        raise ValueError("VEV cannot be negative")
    for upper, klass in MRM_BANDS:
        if vev < upper:
            return klass
    return 7


def market_risk_measure(
    returns: Sequence[float],
    *,
    rhp_years: float,
    periods_per_year: int = 256,
    are_log_returns: bool = False,
) -> MarketRiskResult:
    """Category 2 Market Risk Measure from a return history.

    Parameters
    ----------
    returns:
        Observed returns at the frequency given by ``periods_per_year``. Annex II
        point 10 calls for at least five years of daily prices, or the
        equivalent in weekly or monthly observations.
    rhp_years:
        Recommended holding period in years.
    periods_per_year:
        256 for daily observations, 52 for weekly, 12 for monthly - the
        convention used by the RTS.
    are_log_returns:
        ``True`` if the series is already in log space. Simple returns are
        converted, since the RTS works with log returns throughout.

    Notes
    -----
    Annex II point 12 gives the Cornish-Fisher VaR over ``N`` periods as::

        VaR = sigma * sqrt(N) * (-1.96 + 0.474 * mu1 / sqrt(N)
                                 - 0.0687 * mu2 / N
                                 + 0.146 * mu1^2 / N)
              - 0.5 * sigma^2 * N

    and point 13 converts it into the VaR-equivalent volatility::

        VEV = (sqrt(3.842 - 2 * VaR) - 1.96) / sqrt(T)
    """

    if len(returns) < 4:
        raise ValueError("at least four return observations are required")
    if rhp_years <= 0:
        raise ValueError("the recommended holding period must be positive")

    series = list(returns) if are_log_returns else [math.log1p(r) for r in returns]
    _, sigma, skew, kurt = moments(series)

    periods = rhp_years * periods_per_year
    root_n = math.sqrt(periods)

    var_log = (
        sigma
        * root_n
        * (Z_97_5 + 0.474 * skew / root_n - 0.0687 * kurt / periods + 0.146 * skew**2 / periods)
        - 0.5 * sigma**2 * periods
    )

    vev = (math.sqrt(3.842 - 2 * var_log) - 1.96) / math.sqrt(rhp_years)

    return MarketRiskResult(
        var_log=var_log,
        vev=vev,
        mrm_class=mrm_class_from_vev(vev),
        periods=periods,
        volatility=sigma,
        skewness=skew,
        excess_kurtosis=kurt,
        observations=len(series),
    )


def credit_risk_measure(
    credit_quality_step: int | None = None,
    *,
    maturity_years: float | None = None,
    has_credit_exposure: bool = True,
) -> CreditRiskResult:
    """Credit Risk Measure (Annex II points 39-46).

    A fund whose assets are held by a depositary and which carries no direct
    obligation of an identifiable obligor is assigned the lowest credit risk
    class. Where there is an obligor, the credit quality step of its external
    rating is adjusted for maturity - lowered by one step below one year,
    raised by one step beyond twelve years - and then mapped to a class.
    """

    if not has_credit_exposure:
        return CreditRiskResult(
            crm_class=1,
            credit_quality_step=None,
            adjusted_credit_quality_step=None,
            maturity_years=maturity_years,
            rationale=(
                "No obligor stands behind the product beyond the assets held, so "
                "credit risk class 1 applies (Annex II, point 40)."
            ),
        )

    if credit_quality_step is None:
        raise ValueError(
            "a credit quality step is required when the product carries credit exposure"
        )
    if credit_quality_step not in CQS_TO_CREDIT_CLASS:
        raise ValueError("credit quality step must be an integer from 0 to 6")

    adjusted = credit_quality_step
    note = "no maturity adjustment (maturity between 1 and 12 years)"
    if maturity_years is not None:
        if maturity_years < 1:
            adjusted = max(0, credit_quality_step - 1)
            note = "credit quality step lowered by one for a maturity below one year"
        elif maturity_years > 12:
            adjusted = min(6, credit_quality_step + 1)
            note = "credit quality step raised by one for a maturity beyond twelve years"

    return CreditRiskResult(
        crm_class=CQS_TO_CREDIT_CLASS[adjusted],
        credit_quality_step=credit_quality_step,
        adjusted_credit_quality_step=adjusted,
        maturity_years=maturity_years,
        rationale=f"Credit quality step {credit_quality_step}; {note}.",
    )


def combine(mrm_class: int, crm_class: int) -> int:
    """Read the SRI off the Annex II point 52 matrix."""

    if not 1 <= mrm_class <= 7:
        raise ValueError("market risk class must be between 1 and 7")
    if not 1 <= crm_class <= 6:
        raise ValueError("credit risk class must be between 1 and 6")
    return SRI_MATRIX[crm_class][mrm_class - 1]


def summary_risk_indicator(
    returns: Sequence[float],
    *,
    rhp_years: float,
    periods_per_year: int = 256,
    credit_quality_step: int | None = None,
    maturity_years: float | None = None,
    has_credit_exposure: bool = False,
    are_log_returns: bool = False,
) -> SriResult:
    """Full SRI for a Category 2 PRIIP."""

    market = market_risk_measure(
        returns,
        rhp_years=rhp_years,
        periods_per_year=periods_per_year,
        are_log_returns=are_log_returns,
    )
    credit = credit_risk_measure(
        credit_quality_step,
        maturity_years=maturity_years,
        has_credit_exposure=has_credit_exposure,
    )
    return SriResult(
        sri=combine(market.mrm_class, credit.crm_class),
        market=market,
        credit=credit,
    )
