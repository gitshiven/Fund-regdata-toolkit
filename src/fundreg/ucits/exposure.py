"""Global exposure and counterparty exposure for UCITS.

Global exposure is computed under the *commitment approach* of CESR/10-788
("Guidelines on Risk Measurement and the Calculation of Global Exposure and
Counterparty Risk for UCITS"), which converts every derivative into the market
value of an equivalent position in its underlying, allows netting and hedging
arrangements to offset, and caps the total at 100% of NAV (Art. 51(3) of
Directive 2009/65/EC).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from fundreg.models import DERIVATIVE_CLASSES, Portfolio, Position


@dataclass
class NettingGroup:
    """Derivative commitments sharing a netting set and an underlying."""

    netting_set: str
    underlying_id: str
    gross_long: float = 0.0
    gross_short: float = 0.0
    contracts: list[str] = field(default_factory=list)

    @property
    def gross(self) -> float:
        """Commitment before any offsetting."""
        return self.gross_long + abs(self.gross_short)

    @property
    def net(self) -> float:
        """Commitment after offsetting longs against shorts on the same underlying."""
        return abs(self.gross_long + self.gross_short)

    @property
    def offset(self) -> float:
        return self.gross - self.net


@dataclass
class CommitmentResult:
    """Outcome of the commitment-approach global exposure calculation."""

    nav: float
    gross_commitment: float
    net_commitment: float
    groups: list[NettingGroup]

    @property
    def global_exposure_pct(self) -> float:
        """Global exposure as a percentage of NAV, netted per CESR/10-788."""
        return 100.0 * self.net_commitment / self.nav

    @property
    def gross_exposure_pct(self) -> float:
        return 100.0 * self.gross_commitment / self.nav

    @property
    def leverage_pct(self) -> float:
        """Incremental leverage created by derivatives, i.e. global exposure."""
        return self.global_exposure_pct

    @property
    def total_exposure_pct(self) -> float:
        """Portfolio exposure including derivatives, capped at 200% of NAV."""
        return 100.0 + self.global_exposure_pct

    @property
    def netting_benefit_pct(self) -> float:
        return self.gross_exposure_pct - self.global_exposure_pct


def _derivatives(positions: Iterable[Position]) -> list[Position]:
    return [p for p in positions if p.asset_class in DERIVATIVE_CLASSES]


def commitment_approach_exposure(
    portfolio: Portfolio, *, allow_netting: bool = True
) -> CommitmentResult:
    """Compute global exposure under the commitment approach.

    Each derivative is converted into the market value of the equivalent
    position in its underlying (the ``notional`` on :class:`~fundreg.models.
    Derivative`, signed long/short). Contracts that share both a netting set and
    an underlying are summed with sign before the absolute value is taken, which
    is the offsetting permitted by CESR/10-788 Box 7 for netting and hedging
    arrangements. Contracts with no netting set stand alone.

    Parameters
    ----------
    allow_netting:
        Set to ``False`` to produce the gross figure, which is a useful control
        total and the number a risk team quotes when challenging an offset.
    """

    if portfolio.nav <= 0:
        raise ValueError("NAV must be positive to express exposure as a percentage")

    groups: dict[tuple[str, str], NettingGroup] = {}
    for index, position in enumerate(_derivatives(portfolio)):
        derivative = position.instrument.derivative
        assert derivative is not None  # guaranteed by Instrument.__post_init__
        set_key = derivative.netting_set if allow_netting else None
        key = (set_key or f"__standalone_{index}", derivative.underlying_id)
        group = groups.get(key)
        if group is None:
            group = NettingGroup(
                netting_set=set_key or "(none)", underlying_id=derivative.underlying_id
            )
            groups[key] = group
        if derivative.notional >= 0:
            group.gross_long += derivative.notional
        else:
            group.gross_short += derivative.notional
        group.contracts.append(position.instrument.instrument_id)

    ordered = sorted(groups.values(), key=lambda g: (-g.net, g.underlying_id))
    return CommitmentResult(
        nav=portfolio.nav,
        gross_commitment=sum(g.gross for g in ordered),
        net_commitment=sum(g.net for g in ordered),
        groups=ordered,
    )


def counterparty_exposures(portfolio: Portfolio) -> dict[str, float]:
    """Counterparty risk exposure per OTC counterparty, net of collateral.

    Exposure is the positive mark-to-market replacement value of the contracts
    facing a counterparty, netted within each netting set where a legally
    enforceable netting agreement exists, less eligible collateral received
    (ESMA/2014/937 guidelines on ETFs and other UCITS issues, section on
    counterparty risk and collateral).

    Returns a mapping of counterparty to exposure in base-currency units. Only
    OTC derivatives contribute; exchange-traded contracts face a clearing house
    and are excluded from the Art. 52(1) counterparty limit.
    """

    from fundreg.models import AssetClass

    per_set: dict[tuple[str, str], float] = defaultdict(float)
    collateral: dict[str, float] = defaultdict(float)
    standalone = 0

    for position in portfolio:
        if position.asset_class is not AssetClass.OTC_DERIVATIVE:
            continue
        derivative = position.instrument.derivative
        assert derivative is not None
        counterparty = derivative.counterparty
        if not counterparty:
            continue
        if derivative.netting_set:
            key = (counterparty, derivative.netting_set)
        else:
            standalone += 1
            key = (counterparty, f"__standalone_{standalone}")
        # Replacement value is the mark-to-market of the contract itself, not
        # the notional of the underlying.
        per_set[key] += position.market_value
        collateral[counterparty] += derivative.collateral_received

    gross: dict[str, float] = defaultdict(float)
    for (counterparty, _), value in per_set.items():
        # A netting set with a negative mark-to-market is an obligation of the
        # fund and creates no counterparty exposure.
        gross[counterparty] += max(0.0, value)

    return {
        counterparty: max(0.0, value - collateral.get(counterparty, 0.0))
        for counterparty, value in sorted(gross.items())
    }
