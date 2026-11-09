"""PRIIPs performance scenarios (Annex IV of Delegated Regulation (EU) 2017/653).

Two methodologies live here because both are still in circulation:

``cornish_fisher_scenarios``
    The original 2017 moment-based approach. Favourable, moderate and
    unfavourable are the 90th, 50th and 10th percentiles of a Cornish-Fisher
    expansion of the return distribution. Still the method many internal models
    and back-books were built on, and still the basis of the stress scenario.

``historical_scenarios``
    The approach introduced by Delegated Regulation (EU) 2021/2268, in force for
    KIDs since 1 January 2023. Scenarios are *selected from history*: the
    product is notionally bought at every point in a long history, held for the
    recommended holding period, and the best, median and worst outcomes become
    the favourable, moderate and unfavourable scenarios.

The stress scenario is unchanged by the 2021 amendment and is computed in both
cases from a stressed volatility taken from the tail of the rolling-volatility
distribution.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from fundreg.stats import (
    Z_95,
    Z_99,
    annualise,
    cornish_fisher_quantile,
    moments,
    norm_ppf,
    percentile,
    rolling,
    stdev,
)

ScenarioName = Literal["stress", "unfavourable", "moderate", "favourable"]

SCENARIO_ORDER: tuple[ScenarioName, ...] = (
    "stress",
    "unfavourable",
    "moderate",
    "favourable",
)

SCENARIO_LABELS: dict[str, str] = {
    "stress": "Stress",
    "unfavourable": "Unfavourable",
    "moderate": "Moderate",
    "favourable": "Favourable",
}


@dataclass(frozen=True)
class Scenario:
    """One row of the KID performance-scenario table."""

    name: ScenarioName
    #: Cumulative return over the holding period, as a decimal.
    total_return: float
    #: Years the scenario is measured over.
    years: float
    #: Value of a 10 000 unit investment at the end of the period.
    value: float
    #: Compound annual return over the period.
    annualised_return: float
    #: Where the number came from, for the audit trail.
    basis: str = ""

    @property
    def label(self) -> str:
        return SCENARIO_LABELS[self.name]


@dataclass(frozen=True)
class ScenarioSet:
    """All four scenarios at one holding period."""

    years: float
    investment: float
    scenarios: dict[str, Scenario]
    methodology: str

    def __getitem__(self, name: str) -> Scenario:
        return self.scenarios[name]

    def rows(self) -> list[Scenario]:
        return [self.scenarios[n] for n in SCENARIO_ORDER if n in self.scenarios]

    def to_dict(self) -> dict:
        return {
            "years": self.years,
            "investment": self.investment,
            "methodology": self.methodology,
            "scenarios": {
                name: {
                    "label": s.label,
                    "value": round(s.value, 2),
                    "total_return": round(s.total_return, 6),
                    "annualised_return": round(s.annualised_return, 6),
                    "basis": s.basis,
                }
                for name, s in self.scenarios.items()
            },
        }


def _as_log_returns(returns: Sequence[float], are_log_returns: bool) -> list[float]:
    if are_log_returns:
        return list(returns)
    return [math.log1p(r) for r in returns]


def _scenario(
    name: ScenarioName,
    log_return: float,
    years: float,
    investment: float,
    basis: str,
) -> Scenario:
    total = math.exp(log_return) - 1.0
    return Scenario(
        name=name,
        total_return=total,
        years=years,
        value=investment * math.exp(log_return),
        annualised_return=annualise(total, years),
        basis=basis,
    )


def stress_volatility(
    log_returns: Sequence[float],
    *,
    rhp_years: float,
    periods_per_year: int = 256,
) -> tuple[float, int, float]:
    """Stressed volatility for the stress scenario (Annex IV points 10-11).

    The return history is cut into overlapping sub-intervals of length ``w``
    (21 observations for a holding period of a year or less, 63 beyond that, on
    a daily series). The standard deviation of each sub-interval is computed,
    and the stressed volatility is an extreme percentile of that distribution:
    the 99th for holding periods beyond a year, the 95th at or below a year.

    Returns ``(stressed volatility per period, window length, percentile used)``.
    """

    scale = max(1, round(periods_per_year / 256))
    if rhp_years > 1:
        window, q = 63 * scale, 0.99
    else:
        window, q = 21 * scale, 0.95

    window = min(window, max(2, len(log_returns) // 2))
    vols = [stdev(block) for block in rolling(log_returns, window)]
    if not vols:
        raise ValueError("return history is too short to compute a stressed volatility")
    return percentile(vols, q), window, q


def stress_scenario(
    returns: Sequence[float],
    *,
    rhp_years: float,
    periods_per_year: int = 256,
    investment: float = 10_000.0,
    are_log_returns: bool = False,
) -> Scenario:
    """The PRIIPs stress scenario.

    Uses the stressed volatility in a Cornish-Fisher expansion evaluated at the
    1st percentile for holding periods beyond a year and the 5th percentile at
    or below a year.
    """

    log_returns = _as_log_returns(returns, are_log_returns)
    _, _, skew, kurt = moments(log_returns)
    sigma_s, window, vol_q = stress_volatility(
        log_returns, rhp_years=rhp_years, periods_per_year=periods_per_year
    )

    periods = rhp_years * periods_per_year
    z = Z_99 if rhp_years > 1 else Z_95
    z_cf = cornish_fisher_quantile(z, skew, kurt, periods)
    log_return = sigma_s * math.sqrt(periods) * z_cf - 0.5 * sigma_s**2 * periods

    return _scenario(
        "stress",
        log_return,
        rhp_years,
        investment,
        basis=(
            f"Cornish-Fisher at the {(1 - (0.99 if rhp_years > 1 else 0.95)) * 100:.0f}th "
            f"percentile using a stressed volatility of {sigma_s * math.sqrt(periods_per_year):.2%} "
            f"annualised, taken as the {vol_q:.0%} percentile of {window}-period rolling volatilities"
        ),
    )


def cornish_fisher_scenarios(
    returns: Sequence[float],
    *,
    rhp_years: float,
    periods_per_year: int = 256,
    investment: float = 10_000.0,
    are_log_returns: bool = False,
) -> ScenarioSet:
    """Moment-based scenarios under the original 2017 Annex IV.

    Favourable, moderate and unfavourable are the 90th, 50th and 10th
    percentiles of the Cornish-Fisher expanded distribution of the holding
    period return.
    """

    log_returns = _as_log_returns(returns, are_log_returns)
    mu, sigma, skew, kurt = moments(log_returns)
    periods = rhp_years * periods_per_year

    quantiles: dict[ScenarioName, float] = {
        "unfavourable": 0.10,
        "moderate": 0.50,
        "favourable": 0.90,
    }

    scenarios: dict[str, Scenario] = {}
    for name, q in quantiles.items():
        z_cf = cornish_fisher_quantile(norm_ppf(q), skew, kurt, periods)
        log_return = mu * periods + sigma * math.sqrt(periods) * z_cf - 0.5 * sigma**2 * periods
        scenarios[name] = _scenario(
            name,
            log_return,
            rhp_years,
            investment,
            basis=f"{q:.0%} percentile of the Cornish-Fisher expanded distribution",
        )

    scenarios["stress"] = stress_scenario(
        log_returns,
        rhp_years=rhp_years,
        periods_per_year=periods_per_year,
        investment=investment,
        are_log_returns=True,
    )

    return ScenarioSet(
        years=rhp_years,
        investment=investment,
        scenarios=scenarios,
        methodology="Annex IV (2017 RTS), Category 2 moment-based Cornish-Fisher",
    )


def historical_scenarios(
    returns: Sequence[float],
    *,
    rhp_years: float,
    periods_per_year: int = 256,
    investment: float = 10_000.0,
    are_log_returns: bool = False,
) -> ScenarioSet:
    """Scenarios selected from history, per Delegated Regulation (EU) 2021/2268.

    Every overlapping window of the holding period in the return history is a
    candidate outcome. The worst is the unfavourable scenario, the median is the
    moderate scenario and the best is the favourable scenario. The stress
    scenario is added unchanged from Annex IV.

    Raises
    ------
    ValueError
        If the history is shorter than one holding period, in which case there
        is nothing to select from and the moment-based method should be used.
    """

    log_returns = _as_log_returns(returns, are_log_returns)
    window = int(round(rhp_years * periods_per_year))
    if window < 1:
        raise ValueError("the holding period rounds to fewer than one observation")
    if len(log_returns) < window + 1:
        raise ValueError(
            f"need more than {window} observations to select {rhp_years}-year sub-periods, "
            f"got {len(log_returns)}"
        )

    outcomes = [sum(block) for block in rolling(log_returns, window)]
    ordered = sorted(outcomes)
    count = len(outcomes)

    picks: dict[ScenarioName, tuple[float, str]] = {
        "unfavourable": (ordered[0], f"worst of {count} overlapping sub-periods"),
        "moderate": (percentile(ordered, 0.5), f"median of {count} overlapping sub-periods"),
        "favourable": (ordered[-1], f"best of {count} overlapping sub-periods"),
    }

    scenarios: dict[str, Scenario] = {
        name: _scenario(name, value, rhp_years, investment, basis)
        for name, (value, basis) in picks.items()
    }
    scenarios["stress"] = stress_scenario(
        log_returns,
        rhp_years=rhp_years,
        periods_per_year=periods_per_year,
        investment=investment,
        are_log_returns=True,
    )

    return ScenarioSet(
        years=rhp_years,
        investment=investment,
        scenarios=scenarios,
        methodology="Annex IV as amended by (EU) 2021/2268, sub-period selection from history",
    )


def scenario_table(
    returns: Sequence[float],
    *,
    rhp_years: float,
    periods_per_year: int = 256,
    investment: float = 10_000.0,
    are_log_returns: bool = False,
    method: Literal["historical", "cornish_fisher"] = "historical",
) -> list[ScenarioSet]:
    """Scenarios at one year and at the recommended holding period.

    The KID shows intermediate holding periods where the recommended holding
    period is longer than a year, so this returns one :class:`ScenarioSet` per
    period actually displayed.
    """

    builder = historical_scenarios if method == "historical" else cornish_fisher_scenarios
    periods = [1.0, rhp_years] if rhp_years > 1 else [rhp_years]

    out: list[ScenarioSet] = []
    for years in periods:
        try:
            out.append(
                builder(
                    returns,
                    rhp_years=years,
                    periods_per_year=periods_per_year,
                    investment=investment,
                    are_log_returns=are_log_returns,
                )
            )
        except ValueError:
            # Fall back to the moment-based method where the history is too
            # short to select sub-periods, which is what a practitioner does.
            out.append(
                cornish_fisher_scenarios(
                    returns,
                    rhp_years=years,
                    periods_per_year=periods_per_year,
                    investment=investment,
                    are_log_returns=are_log_returns,
                )
            )
    return out
