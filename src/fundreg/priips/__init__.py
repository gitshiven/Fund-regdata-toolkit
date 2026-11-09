"""PRIIPs Key Information Document calculations.

Commission Delegated Regulation (EU) 2017/653 as amended by (EU) 2021/2268:
summary risk indicator, performance scenarios, and the cost tables.
"""

from fundreg.priips.costs import CostRow, CostStructure, costs_over_time, reduction_in_yield
from fundreg.priips.kid import Kid, ProductInfo, build_kid, render_html, write_html
from fundreg.priips.scenarios import (
    Scenario,
    ScenarioSet,
    cornish_fisher_scenarios,
    historical_scenarios,
    scenario_table,
    stress_scenario,
)
from fundreg.priips.sri import (
    CreditRiskResult,
    MarketRiskResult,
    SriResult,
    combine,
    credit_risk_measure,
    market_risk_measure,
    mrm_class_from_vev,
    summary_risk_indicator,
)

__all__ = [
    "CostRow",
    "CostStructure",
    "CreditRiskResult",
    "Kid",
    "MarketRiskResult",
    "ProductInfo",
    "Scenario",
    "ScenarioSet",
    "SriResult",
    "build_kid",
    "combine",
    "cornish_fisher_scenarios",
    "costs_over_time",
    "credit_risk_measure",
    "historical_scenarios",
    "market_risk_measure",
    "mrm_class_from_vev",
    "reduction_in_yield",
    "render_html",
    "scenario_table",
    "stress_scenario",
    "summary_risk_indicator",
    "write_html",
]
