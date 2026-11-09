"""Small statistics helpers, written out so the toolkit stays dependency-free.

Only what the PRIIPs methodology actually needs: the first four moments of a
return series, the inverse normal CDF, and the Cornish-Fisher quantile expansion
that Annex II and Annex IV of Delegated Regulation (EU) 2017/653 are built on.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

#: Standard normal quantiles used throughout the PRIIPs RTS.
Z_97_5 = -1.959963984540054  # 2.5th percentile, the PRIIPs VaR confidence level
Z_99 = -2.3263478740408408
Z_95 = -1.6448536269514722
Z_90 = -1.2815515655446004


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean of an empty series")
    return sum(values) / len(values)


def stdev(values: Sequence[float], *, population: bool = True) -> float:
    """Standard deviation.

    The PRIIPs RTS defines the volatility of the return series as the population
    standard deviation of the observed returns, so that is the default.
    """

    n = len(values)
    if n < 2:
        raise ValueError("standard deviation needs at least two observations")
    mu = mean(values)
    total = sum((v - mu) ** 2 for v in values)
    return math.sqrt(total / (n if population else n - 1))


def skewness(values: Sequence[float]) -> float:
    """Population skewness, the ``mu1`` of the PRIIPs Cornish-Fisher expansion."""

    n = len(values)
    if n < 3:
        raise ValueError("skewness needs at least three observations")
    mu = mean(values)
    sigma = stdev(values)
    if sigma == 0:
        return 0.0
    return sum((v - mu) ** 3 for v in values) / (n * sigma**3)


def excess_kurtosis(values: Sequence[float]) -> float:
    """Population excess kurtosis, the ``mu2`` of the expansion (normal = 0)."""

    n = len(values)
    if n < 4:
        raise ValueError("kurtosis needs at least four observations")
    mu = mean(values)
    sigma = stdev(values)
    if sigma == 0:
        return 0.0
    return sum((v - mu) ** 4 for v in values) / (n * sigma**4) - 3.0


def moments(values: Sequence[float]) -> tuple[float, float, float, float]:
    """Return ``(mean, volatility, skewness, excess kurtosis)``."""

    return mean(values), stdev(values), skewness(values), excess_kurtosis(values)


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile, ``q`` expressed as a fraction in [0, 1]."""

    if not values:
        raise ValueError("percentile of an empty series")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must lie in [0, 1]")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def median(values: Sequence[float]) -> float:
    return percentile(values, 0.5)


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF.

    Uses the rational approximation of Peter Acklam, refined by one step of
    Halley's method, which is accurate to roughly 1e-15 across the whole range.
    """

    if not 0.0 < p < 1.0:
        raise ValueError("p must lie strictly between 0 and 1")

    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )

    low, high = 0.02425, 1 - 0.02425
    if p < low:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    elif p <= high:
        q = p - 0.5
        r = q * q
        x = (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
            * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
        )
    else:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )

    # One Halley refinement against the true CDF.
    error = 0.5 * math.erfc(-x / math.sqrt(2)) - p
    u = error * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    return x - u / (1 + x * u / 2)


def cornish_fisher_quantile(z: float, skew: float, kurtosis: float, periods: float) -> float:
    """Cornish-Fisher adjusted quantile used by PRIIPs Annex IV.

    Expands the normal quantile ``z`` by the skewness and excess kurtosis of the
    return series, scaled by the number of ``periods`` in the holding period:

    ``z + (z^2 - 1)/6 * mu1/sqrt(N) + (z^3 - 3z)/24 * mu2/N
    - (2z^3 - 5z)/36 * mu1^2/N``
    """

    if periods <= 0:
        raise ValueError("periods must be positive")
    root_n = math.sqrt(periods)
    return (
        z
        + (z**2 - 1) / 6 * skew / root_n
        + (z**3 - 3 * z) / 24 * kurtosis / periods
        - (2 * z**3 - 5 * z) / 36 * skew**2 / periods
    )


def rolling(values: Sequence[float], window: int):
    """Yield successive overlapping windows of length ``window``."""

    if window <= 0:
        raise ValueError("window must be positive")
    for start in range(0, len(values) - window + 1):
        yield values[start : start + window]


def annualise(total_return: float, years: float) -> float:
    """Convert a cumulative return over ``years`` into an annualised rate."""

    if years <= 0:
        raise ValueError("years must be positive")
    return (1.0 + total_return) ** (1.0 / years) - 1.0
