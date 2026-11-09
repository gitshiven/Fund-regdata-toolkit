"""AIFMD leverage: the gross method and the commitment method.

Articles 6 to 8 of Commission Delegated Regulation (EU) 231/2013, which supplement
Article 4(1)(v) of Directive 2011/61/EU.

This is *not* the same measurement as the UCITS global exposure already in
:mod:`fundreg.ucits.exposure`, and conflating the two is one of the more common
mistakes in fund reporting:

===========================  ======================================  ==============================
                             UCITS, commitment approach              AIFMD, commitment method
===========================  ======================================  ==============================
What is measured             Incremental exposure from derivatives   Exposure of the whole portfolio
Cash and cash equivalents    Not relevant, derivatives only          Excluded explicitly
Borrowing                    Separate 10% limit under Art. 83        Included where it is reinvested
Expressed as                 Percentage of NAV, capped at 100%       Ratio to NAV, no hard cap
Unleveraged reads as         0%                                      1.0
===========================  ======================================  ==============================

A UCITS with a 27% global exposure is not a fund with 0.27x leverage; on the
AIFMD basis the same book is around 1.3x, because the physical portfolio counts
too.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

from fundreg.models import DERIVATIVE_CLASSES, AssetClass, Portfolio
from fundreg.ucits.exposure import commitment_approach_exposure

#: Positions treated as cash or cash equivalents and so excluded from exposure
#: under Art. 7(a) and Art. 8(2), where they are highly liquid, held in the base
#: currency, convertible to a known amount of cash and subject to an
#: insignificant risk of change in value.
DEFAULT_CASH_EQUIVALENT_CLASSES = frozenset({AssetClass.CASH, AssetClass.DEPOSIT})

#: Art. 111(1) of Regulation (EU) 231/2013: an AIF is "employing leverage on a
#: substantial basis" when its commitment-method exposure exceeds three times
#: its net asset value, which triggers the Art. 24(4) reporting duty.
SUBSTANTIAL_LEVERAGE_RATIO = 3.0


@dataclass
class LeverageConfig:
    """Choices the AIFM has to make and document when measuring leverage."""

    #: Which asset classes count as cash equivalents. Narrow this where a
    #: deposit is term-locked or not held in the base currency, since it is then
    #: an investment rather than a cash equivalent.
    cash_equivalent_classes: frozenset[AssetClass] = DEFAULT_CASH_EQUIVALENT_CLASSES
    #: Art. 7(c) and Art. 8(2): borrowing that stays in cash, where the amount
    #: payable is known, is excluded. Borrowing that has been reinvested adds to
    #: exposure. Default assumes it is reinvested, which is the conservative read.
    borrowing_reinvested: bool = True
    #: Set False to keep a cash equivalent inside the exposure figure, which is
    #: occasionally required where a regulator disputes the classification.
    exclude_cash_equivalents: bool = True


@dataclass
class ExposureComponent:
    """One contributor to the exposure figure, kept for the audit trail."""

    label: str
    amount: float
    note: str = ""


@dataclass
class LeverageResult:
    """Exposure and leverage under one of the two methods."""

    method: str
    nav: float
    exposure: float
    components: list[ExposureComponent] = field(default_factory=list)
    excluded: list[ExposureComponent] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        """Leverage as a multiple of NAV. 1.0 means the AIF is unleveraged."""
        return self.exposure / self.nav

    @property
    def pct_of_nav(self) -> float:
        return 100.0 * self.exposure / self.nav

    @property
    def substantially_leveraged(self) -> bool:
        """Art. 111(1): commitment leverage above three times NAV."""
        return self.ratio > SUBSTANTIAL_LEVERAGE_RATIO

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "nav": self.nav,
            "exposure": round(self.exposure, 2),
            "ratio": round(self.ratio, 6),
            "pct_of_nav": round(self.pct_of_nav, 4),
            "substantially_leveraged": self.substantially_leveraged,
            "components": [
                {"label": c.label, "amount": round(c.amount, 2), "note": c.note}
                for c in self.components
            ],
            "excluded": [
                {"label": c.label, "amount": round(c.amount, 2), "note": c.note}
                for c in self.excluded
            ],
        }


@dataclass
class LeverageProfile:
    """Both methods side by side, which is what Annex IV asks for."""

    gross: LeverageResult
    commitment: LeverageResult
    #: The UCITS figure, included so the two bases can be compared directly.
    ucits_global_exposure_pct: float | None = None

    @property
    def substantially_leveraged(self) -> bool:
        return self.commitment.substantially_leveraged

    def to_dict(self) -> dict:
        return {
            "gross": self.gross.to_dict(),
            "commitment": self.commitment.to_dict(),
            "ucits_global_exposure_pct": self.ucits_global_exposure_pct,
            "substantially_leveraged": self.substantially_leveraged,
        }


def _physical_and_cash(portfolio: Portfolio, config: LeverageConfig) -> tuple[float, float, float]:
    """Split non-derivative holdings into exposure, cash equivalents and borrowing."""

    physical = 0.0
    cash = 0.0
    for position in portfolio:
        if position.asset_class in DERIVATIVE_CLASSES:
            continue
        if position.asset_class in config.cash_equivalent_classes:
            cash += position.market_value
        else:
            physical += abs(position.market_value)

    borrowing = portfolio.borrowing if config.borrowing_reinvested else 0.0
    return physical, cash, borrowing


def _common_components(
    portfolio: Portfolio, config: LeverageConfig
) -> tuple[list[ExposureComponent], list[ExposureComponent], float]:
    physical, cash, borrowing = _physical_and_cash(portfolio, config)

    components = [
        ExposureComponent(
            "Physical positions",
            physical,
            "Absolute market value of non-derivative holdings.",
        )
    ]
    excluded: list[ExposureComponent] = []

    if config.exclude_cash_equivalents:
        excluded.append(
            ExposureComponent(
                "Cash and cash equivalents",
                cash,
                "Excluded under Art. 7(a) and Art. 8(2) of Regulation (EU) 231/2013.",
            )
        )
    else:
        components.append(
            ExposureComponent("Cash and cash equivalents", abs(cash), "Retained by configuration.")
        )

    if borrowing:
        components.append(
            ExposureComponent(
                "Cash borrowing reinvested",
                borrowing,
                "Included under Art. 7(d): borrowing that has been put to work adds exposure.",
            )
        )
    elif portfolio.borrowing:
        excluded.append(
            ExposureComponent(
                "Cash borrowing held in cash",
                portfolio.borrowing,
                "Excluded under Art. 7(c): the amount payable is known and it remains in cash.",
            )
        )

    total = sum(c.amount for c in components)
    return components, excluded, total


def gross_method_exposure(
    portfolio: Portfolio, config: LeverageConfig | None = None
) -> LeverageResult:
    """Exposure under the gross method (Art. 7 of Regulation (EU) 231/2013).

    Every derivative is converted into the market value of an equivalent
    position in its underlying and taken at absolute value. No netting and no
    hedging: a long and a short on the same index both add exposure. That is the
    point of the measure, which is meant to show the size of the book before any
    offsetting argument is made.
    """

    config = config or LeverageConfig()
    if portfolio.nav <= 0:
        raise ValueError("NAV must be positive to express leverage as a ratio")

    components, excluded, _ = _common_components(portfolio, config)

    derivative_total = 0.0
    for position in portfolio:
        if position.asset_class not in DERIVATIVE_CLASSES:
            continue
        derivative = position.instrument.derivative
        assert derivative is not None
        derivative_total += abs(derivative.notional)

    components.append(
        ExposureComponent(
            "Derivative notional, no offsetting",
            derivative_total,
            "Converted to the equivalent underlying position under Annex I, taken gross.",
        )
    )

    return LeverageResult(
        method="gross",
        nav=portfolio.nav,
        exposure=sum(c.amount for c in components),
        components=components,
        excluded=excluded,
    )


def commitment_method_exposure(
    portfolio: Portfolio, config: LeverageConfig | None = None
) -> LeverageResult:
    """Exposure under the commitment method (Art. 8 of Regulation (EU) 231/2013).

    Identical to the gross method except that netting and hedging arrangements
    may offset, exactly as under CESR/10-788 for a UCITS. The derivative leg is
    therefore borrowed from :func:`fundreg.ucits.exposure.commitment_approach_exposure`,
    which already implements that offsetting; what differs is everything around
    it, since AIFMD measures the whole portfolio rather than the increment.
    """

    config = config or LeverageConfig()
    if portfolio.nav <= 0:
        raise ValueError("NAV must be positive to express leverage as a ratio")

    components, excluded, _ = _common_components(portfolio, config)
    derivatives = commitment_approach_exposure(portfolio)

    components.append(
        ExposureComponent(
            "Derivative commitment, after netting",
            derivatives.net_commitment,
            (
                f"Gross {derivatives.gross_commitment:,.0f} less "
                f"{derivatives.gross_commitment - derivatives.net_commitment:,.0f} "
                "offset by netting and hedging arrangements."
            ),
        )
    )

    return LeverageResult(
        method="commitment",
        nav=portfolio.nav,
        exposure=sum(c.amount for c in components),
        components=components,
        excluded=excluded,
    )


def leverage_profile(portfolio: Portfolio, config: LeverageConfig | None = None) -> LeverageProfile:
    """Both AIFMD methods plus the UCITS figure, for comparison."""

    config = config or LeverageConfig()
    ucits = None
    with contextlib.suppress(ValueError):
        # A zero NAV makes the UCITS percentage undefined; the AIFMD ratios
        # below raise on the same portfolio, so there is nothing to report here.
        ucits = round(commitment_approach_exposure(portfolio).global_exposure_pct, 4)

    return LeverageProfile(
        gross=gross_method_exposure(portfolio, config),
        commitment=commitment_method_exposure(portfolio, config),
        ucits_global_exposure_pct=ucits,
    )
