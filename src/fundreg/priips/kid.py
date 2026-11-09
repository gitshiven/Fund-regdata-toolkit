"""Assemble and render a PRIIPs Key Information Document.

The layout follows Annex I of Delegated Regulation (EU) 2017/653: what the
product is, the risk indicator, performance scenarios, what happens on default,
costs, how long to hold it, and how to complain.

The output is a self-contained HTML page. It is a working rendering of real
calculations, not a filing: a KID that goes to investors needs the issuer legal
text, the regulator-mandated wording in the local language and a competent
person to sign it off.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from fundreg.priips.costs import CostRow, CostStructure, costs_over_time
from fundreg.priips.scenarios import ScenarioSet, scenario_table
from fundreg.priips.sri import SriResult, summary_risk_indicator


@dataclass
class ProductInfo:
    """Section 1 of the KID: what this product is."""

    name: str
    isin: str
    manufacturer: str
    manufacturer_website: str = ""
    competent_authority: str = ""
    objectives: str = ""
    intended_investor: str = ""
    term: str = "This product has no maturity date."
    currency: str = "EUR"
    #: Recommended holding period in years.
    rhp_years: float = 5.0
    document_date: str = field(default_factory=lambda: date.today().isoformat())


@dataclass
class Kid:
    """A computed KID, ready to render."""

    product: ProductInfo
    sri: SriResult
    scenario_sets: list[ScenarioSet]
    cost_rows: list[CostRow]
    investment: float = 10_000.0

    @property
    def rhp_scenarios(self) -> ScenarioSet:
        return self.scenario_sets[-1]

    def to_dict(self) -> dict:
        return {
            "product": {
                "name": self.product.name,
                "isin": self.product.isin,
                "manufacturer": self.product.manufacturer,
                "currency": self.product.currency,
                "rhp_years": self.product.rhp_years,
                "document_date": self.product.document_date,
            },
            "risk": self.sri.to_dict(),
            "scenarios": [s.to_dict() for s in self.scenario_sets],
            "costs": [c.to_dict() for c in self.cost_rows],
        }


def build_kid(
    product: ProductInfo,
    returns: Sequence[float],
    costs: CostStructure,
    *,
    periods_per_year: int = 256,
    investment: float = 10_000.0,
    credit_quality_step: int | None = None,
    has_credit_exposure: bool = False,
    scenario_method: str = "historical",
) -> Kid:
    """Compute every number the KID needs from a return history and a fee schedule."""

    sri = summary_risk_indicator(
        returns,
        rhp_years=product.rhp_years,
        periods_per_year=periods_per_year,
        credit_quality_step=credit_quality_step,
        has_credit_exposure=has_credit_exposure,
    )
    sets = scenario_table(
        returns,
        rhp_years=product.rhp_years,
        periods_per_year=periods_per_year,
        investment=investment,
        method="historical" if scenario_method == "historical" else "cornish_fisher",
    )
    # Annex VI assumes the moderate scenario as the gross return for the cost table.
    moderate = sets[-1]["moderate"].annualised_return
    rows = costs_over_time(
        costs,
        rhp_years=product.rhp_years,
        gross_return=moderate,
        investment=investment,
    )
    return Kid(
        product=product,
        sri=sri,
        scenario_sets=sets,
        cost_rows=rows,
        investment=investment,
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

_CSS = """
:root{--ink:#14181f;--muted:#5a6472;--line:#d9dee6;--bg:#fff;--panel:#f6f8fb;
--accent:#1f4e79;--warn:#b4531a;--bad:#9a1f2e;--good:#1d6b45}
*{box-sizing:border-box}
body{margin:0;padding:32px 16px;background:#eef1f6;color:var(--ink);
font:15px/1.55 "Segoe UI",system-ui,-apple-system,sans-serif}
.kid{max-width:860px;margin:0 auto;background:var(--bg);border:1px solid var(--line);
border-radius:10px;padding:36px 40px}
h1{font-size:25px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:17px;margin:34px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--accent);
color:var(--accent)}
.sub{color:var(--muted);font-size:13.5px;margin:0 0 6px}
.lead{background:var(--panel);border-left:3px solid var(--accent);padding:12px 16px;
font-size:13.5px;margin:18px 0}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:13.5px}
th,td{border:1px solid var(--line);padding:8px 10px;text-align:right}
th{background:var(--panel);font-weight:600;text-align:right}
th:first-child,td:first-child{text-align:left}
tbody tr:nth-child(even){background:#fbfcfe}
.scale{display:flex;gap:4px;margin:14px 0 6px}
.scale span{flex:1;text-align:center;padding:11px 0;border:1px solid var(--line);
border-radius:4px;font-weight:600;color:var(--muted);background:var(--panel)}
.scale span.on{background:var(--accent);border-color:var(--accent);color:#fff}
.ends{display:flex;justify-content:space-between;font-size:12px;color:var(--muted)}
.neg{color:var(--bad)}.pos{color:var(--good)}
.note{font-size:12.5px;color:var(--muted);margin-top:8px}
.foot{margin-top:34px;padding-top:14px;border-top:1px solid var(--line);
font-size:12px;color:var(--muted)}
dl{display:grid;grid-template-columns:200px 1fr;gap:6px 18px;font-size:13.5px;margin:12px 0}
dt{color:var(--muted)}dd{margin:0}
@media print{body{background:#fff;padding:0}.kid{border:0;padding:0}}
"""


def _pct(value: float, places: int = 1) -> str:
    cls = "neg" if value < 0 else "pos"
    return f'<span class="{cls}">{value * 100:.{places}f}%</span>'


def _money(value: float, currency: str) -> str:
    return f"{currency} {value:,.0f}"


def _esc(text: str) -> str:
    return html.escape(text or "")


def render_html(kid: Kid) -> str:
    """Render a KID as a standalone HTML page."""

    p = kid.product
    sri = kid.sri

    scale = "".join(f'<span class="{"on" if n == sri.sri else ""}">{n}</span>' for n in range(1, 8))

    scenario_blocks = []
    for sset in kid.scenario_sets:
        label = f"If you exit after {sset.years:g} year{'s' if sset.years != 1 else ''}"
        rows = "".join(
            f"<tr><td>{s.label}</td>"
            f"<td>{_money(s.value, p.currency)}</td>"
            f"<td>{_pct(s.annualised_return)}</td></tr>"
            for s in sset.rows()
        )
        scenario_blocks.append(
            f"<h3 style='font-size:14px;margin:18px 0 4px'>{label}</h3>"
            f"<table><thead><tr><th>Scenario</th><th>What you might get back</th>"
            f"<th>Average return each year</th></tr></thead><tbody>{rows}</tbody></table>"
            f"<p class='note'>{_esc(sset.methodology)}</p>"
        )

    cost_rows = "".join(
        f"<tr><td>If you exit after {c.years:g} year{'s' if c.years != 1 else ''}</td>"
        f"<td>{_money(c.total_costs, p.currency)}</td>"
        f"<td>{c.riy * 100:.2f}%</td></tr>"
        for c in kid.cost_rows
    )

    rhp_costs = kid.cost_rows[-1]
    composition = "".join(
        f"<tr><td>{label.replace('_', ' ').capitalize()}</td><td>{value * 100:.2f}%</td></tr>"
        for label, value in rhp_costs.composition.items()
        if abs(value) > 1e-9
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Key Information Document - {_esc(p.name)}</title>
<style>{_CSS}</style></head><body>
<main class="kid">
  <h1>Key Information Document</h1>
  <p class="sub"><strong>{_esc(p.name)}</strong> &mdash; ISIN {_esc(p.isin)}</p>
  <p class="sub">{_esc(p.manufacturer)}{
        " &middot; " + _esc(p.manufacturer_website) if p.manufacturer_website else ""
    }</p>
  <div class="lead">
    <strong>Purpose.</strong> This document provides you with key information about this
    investment product. It is not marketing material. The information is required by law
    to help you understand the nature, risks, costs, potential gains and losses of this
    product and to help you compare it with other products.
  </div>

  <h2>What is this product?</h2>
  <dl>
    <dt>Type</dt><dd>{_esc(p.term)}</dd>
    <dt>Objectives</dt><dd>{_esc(p.objectives)}</dd>
    <dt>Intended retail investor</dt><dd>{_esc(p.intended_investor)}</dd>
    <dt>Currency</dt><dd>{_esc(p.currency)}</dd>
  </dl>

  <h2>What are the risks and what could I get in return?</h2>
  <div class="scale">{scale}</div>
  <div class="ends"><span>Lower risk</span><span>Higher risk</span></div>
  <p>{_esc(sri.narrative)} The risk indicator assumes you keep the product for
     {p.rhp_years:g} years.</p>
  <p class="note">
    Market risk class {sri.market.mrm_class} (VaR-equivalent volatility
    {sri.market.vev:.2%} annualised, computed from {sri.market.observations}
    observations) combined with credit risk class {sri.credit.crm_class}.
    {_esc(sri.credit.rationale)}
  </p>

  <h2>Performance scenarios</h2>
  <p class="note">The figures shown assume an investment of
     {_money(kid.investment, p.currency)}. They are illustrations based on past results
     and on certain assumptions. What you get will vary depending on how the market
     performs and how long you keep the product.</p>
  {"".join(scenario_blocks)}

  <h2>What happens if the manufacturer is unable to pay out?</h2>
  <p>The assets of the fund are held separately by the depositary and are not available
     to the creditors of the manufacturer. You may still face a financial loss if the
     value of the underlying assets falls. There is no compensation or guarantee scheme
     covering that loss.</p>

  <h2>What are the costs?</h2>
  <h3 style="font-size:14px;margin:18px 0 4px">Costs over time</h3>
  <table><thead><tr><th></th><th>Total costs</th><th>Annual cost impact</th></tr></thead>
  <tbody>{cost_rows}</tbody></table>
  <h3 style="font-size:14px;margin:18px 0 4px">Composition of costs</h3>
  <table><thead><tr><th>Cost category</th>
  <th>Annual cost impact if you exit after {rhp_costs.years:g} years</th></tr></thead>
  <tbody>{composition}</tbody></table>
  <p class="note">The annual cost impact is the reduction in yield: the difference between
     the return you would earn with no costs and the return after all costs are charged.</p>

  <h2>How long should I hold it and can I take money out early?</h2>
  <p>Recommended holding period: <strong>{p.rhp_years:g} years</strong>. The product is
     designed for investors who can leave their money invested for that period. Redeeming
     earlier increases the cost impact, as the table above shows.</p>

  <h2>How can I complain?</h2>
  <p>Complaints may be addressed to {_esc(p.manufacturer)}
     {("at " + _esc(p.manufacturer_website)) if p.manufacturer_website else ""}.
     {
        ("The product is authorised in respect of " + _esc(p.competent_authority) + ".")
        if p.competent_authority
        else ""
    }</p>

  <p class="foot">Document date {_esc(p.document_date)}. Generated by
     <strong>fund-regdata-toolkit</strong> as a demonstration of the PRIIPs calculation
     methodology. Not a regulatory filing and not investment advice.</p>
</main></body></html>
"""


def write_html(kid: Kid, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(kid), encoding="utf-8")
    return path
