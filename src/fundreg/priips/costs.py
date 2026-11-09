"""PRIIPs cost disclosure and Reduction in Yield (Annex VI of (EU) 2017/653).

The KID expresses costs two ways: as a cash amount and as a "reduction in
yield" (RIY), the annualised return the investor gives up to costs. RIY is the
difference between the gross annualised return and the annualised return net of
every cost, which is why it cannot be read off a TER - entry and exit costs are
amortised over the holding period and so bite harder on a one-year exit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from fundreg.stats import annualise


@dataclass
class CostStructure:
    """Costs of a share class, expressed as decimals (0.015 is 1.5%).

    The grouping follows the Annex VI cost categories reproduced in the KID
    "Composition of costs" table.
    """

    #: One-off cost on subscription.
    entry_cost: float = 0.0
    #: One-off cost on redemption.
    exit_cost: float = 0.0
    #: Annual management fee and other recurring administrative costs.
    management_fee: float = 0.0
    #: Annual portfolio transaction costs, per Annex VI points 7-23.
    transaction_cost: float = 0.0
    #: Annual performance fee, averaged as required by Annex VI point 7(b).
    performance_fee: float = 0.0
    #: Carried interest, relevant to closed-ended and alternative structures.
    carried_interest: float = 0.0

    @property
    def ongoing(self) -> float:
        """Recurring costs charged each year."""
        return self.management_fee + self.transaction_cost

    @property
    def incidental(self) -> float:
        """Performance-related costs."""
        return self.performance_fee + self.carried_interest

    @property
    def annual_total(self) -> float:
        return self.ongoing + self.incidental


@dataclass(frozen=True)
class CostRow:
    """Costs and RIY at one holding period."""

    years: float
    investment: float
    #: Total costs borne over the period, in currency units.
    total_costs: float
    #: Reduction in yield, as a decimal.
    riy: float
    gross_annualised_return: float
    net_annualised_return: float
    final_value_gross: float
    final_value_net: float
    #: RIY split by cost category.
    composition: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "years": self.years,
            "investment": round(self.investment, 2),
            "total_costs": round(self.total_costs, 2),
            "riy": round(self.riy, 6),
            "riy_pct": round(100 * self.riy, 4),
            "gross_annualised_return": round(self.gross_annualised_return, 6),
            "net_annualised_return": round(self.net_annualised_return, 6),
            "final_value_gross": round(self.final_value_gross, 2),
            "final_value_net": round(self.final_value_net, 2),
            "composition": {k: round(v, 6) for k, v in self.composition.items()},
        }


def _terminal_value(
    investment: float,
    gross_return: float,
    years: float,
    costs: CostStructure,
    *,
    apply_costs: bool = True,
) -> float:
    """Project an investment forward with or without costs.

    Costs are applied as the KID assumes them: the entry cost comes off the
    top, recurring and incidental costs accrue against net asset value through
    the year and so reduce the growth rate directly, and the exit cost comes
    off the final redemption. Charging the recurring cost as a rate rather than
    as a year-end deduction is what makes the reduction in yield of a product
    with no entry or exit cost equal to its annual charge, which is the
    reconciliation a reviewer checks first.
    """

    if not apply_costs:
        return investment * (1.0 + gross_return) ** years

    value = investment * (1.0 - costs.entry_cost)
    value *= (1.0 + gross_return - costs.annual_total) ** years
    return value * (1.0 - costs.exit_cost)


def reduction_in_yield(
    costs: CostStructure,
    *,
    years: float,
    gross_return: float,
    investment: float = 10_000.0,
) -> CostRow:
    """Reduction in yield over ``years`` at an assumed gross annual return.

    Annex VI point 70 onwards: RIY is the gross annualised return less the
    annualised return an investor actually realises once every cost has been
    charged. The ``gross_return`` should be the moderate performance scenario,
    which is what the KID uses.
    """

    if years <= 0:
        raise ValueError("years must be positive")

    gross_value = _terminal_value(investment, gross_return, years, costs, apply_costs=False)
    net_value = _terminal_value(investment, gross_return, years, costs, apply_costs=True)

    gross_annualised = annualise(gross_value / investment - 1.0, years)
    net_annualised = annualise(net_value / investment - 1.0, years)

    # Attribute RIY to each category by removing one category at a time and
    # measuring how much of the yield gap it accounts for. The parts are then
    # rescaled to sum to the total, which is how the KID table is expected to
    # reconcile.
    total_riy = gross_annualised - net_annualised
    categories = {
        "entry_costs": CostStructure(entry_cost=costs.entry_cost),
        "exit_costs": CostStructure(exit_cost=costs.exit_cost),
        "management_fees": CostStructure(management_fee=costs.management_fee),
        "transaction_costs": CostStructure(transaction_cost=costs.transaction_cost),
        "performance_fees": CostStructure(performance_fee=costs.performance_fee),
        "carried_interest": CostStructure(carried_interest=costs.carried_interest),
    }

    parts: dict[str, float] = {}
    for label, single in categories.items():
        value = _terminal_value(investment, gross_return, years, single, apply_costs=True)
        parts[label] = gross_annualised - annualise(value / investment - 1.0, years)

    part_total = sum(parts.values())
    if part_total > 0:
        parts = {k: v * total_riy / part_total for k, v in parts.items()}

    return CostRow(
        years=years,
        investment=investment,
        total_costs=gross_value - net_value,
        riy=total_riy,
        gross_annualised_return=gross_annualised,
        net_annualised_return=net_annualised,
        final_value_gross=gross_value,
        final_value_net=net_value,
        composition=parts,
    )


def costs_over_time(
    costs: CostStructure,
    *,
    rhp_years: float,
    gross_return: float,
    investment: float = 10_000.0,
) -> list[CostRow]:
    """The KID "Costs over time" table: exit after one year, and at the RHP.

    Where the recommended holding period is longer than three years the KID also
    shows the mid-point, so that is included too.
    """

    periods = {1.0, rhp_years}
    if rhp_years > 3:
        periods.add(float(math.ceil(rhp_years / 2)))
    return [
        reduction_in_yield(costs, years=years, gross_return=gross_return, investment=investment)
        for years in sorted(p for p in periods if p >= 1)
    ]
