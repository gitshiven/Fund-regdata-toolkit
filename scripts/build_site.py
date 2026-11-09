"""Build the published dashboard and the reports it links to.

Runs every engine over the sample data and writes the result into ``docs/``,
which GitHub Pages serves:

``docs/data/funds.json``   the dashboard payload
``docs/kid/<fund>.html``   a rendered PRIIPs KID per share class
``docs/reports/*.html``    UCITS, EET, EMT and reconciliation reports

Run from the repository root::

    python scripts/build_site.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from fundreg import __version__, load_portfolio, load_returns  # noqa: E402
from fundreg.priips import (  # noqa: E402
    CostStructure,
    ProductInfo,
    build_kid,
    write_html,
)
from fundreg.templates import (  # noqa: E402
    cross_template_findings,
    load_eet_spec,
    load_emt_spec,
    read_delimited,
    validate_file,
    write_findings_html,
)
from fundreg.ucits import (  # noqa: E402
    check_portfolio,
    commitment_approach_exposure,
)
from generate_sample_data import FUNDS, isin  # noqa: E402

DATA = ROOT / "data"
DOCS = ROOT / "docs"

# Fee schedules per share class, matching the EMT sample.
COSTS = {
    "meridian_european_equity": CostStructure(
        entry_cost=0.0, management_fee=0.0105, transaction_cost=0.0018
    ),
    "meridian_climate_transition": CostStructure(
        entry_cost=0.02, management_fee=0.0140, transaction_cost=0.0026, performance_fee=0.0015
    ),
    "meridian_global_bond": CostStructure(
        entry_cost=0.0, exit_cost=0.005, management_fee=0.0062, transaction_cost=0.0009
    ),
}

OBJECTIVES = {
    "meridian_european_equity": (
        "Long term capital growth by investing at least two thirds of assets in "
        "equities of companies domiciled or principally active in Europe."
    ),
    "meridian_climate_transition": (
        "Sustainable investment objective of contributing to the transition to a "
        "low carbon economy, measured by the weighted carbon intensity of holdings."
    ),
    "meridian_global_bond": (
        "Income and modest capital growth from a diversified portfolio of global "
        "investment grade government and corporate debt."
    ),
}

INVESTORS = {
    "meridian_european_equity": (
        "Retail and professional investors with basic knowledge who can leave the "
        "investment for five years and can bear the loss of the capital invested."
    ),
    "meridian_climate_transition": (
        "Informed investors with a seven year horizon seeking a sustainable "
        "investment objective and able to bear the loss of the capital invested."
    ),
    "meridian_global_bond": (
        "Investors seeking income over a three year horizon who can bear a limited loss of capital."
    ),
}


def build_kids() -> list[dict]:
    """Compute a KID per share class and return the dashboard payload."""

    out: list[dict] = []
    (DOCS / "kid").mkdir(parents=True, exist_ok=True)

    for fund in FUNDS:
        key = fund["key"]
        returns = load_returns(DATA / "returns" / f"{key}.csv")
        product = ProductInfo(
            name=fund["name"],
            isin=isin(fund["isin_body"]),
            manufacturer="Meridian Asset Management S.A.",
            manufacturer_website="https://example.invalid/meridian",
            competent_authority="the Commission de Surveillance du Secteur Financier",
            objectives=OBJECTIVES[key],
            intended_investor=INVESTORS[key],
            rhp_years=fund["rhp_years"],
            term="Open-ended UCITS sub-fund with no maturity date.",
        )
        kid = build_kid(product, returns, COSTS[key])
        write_html(kid, DOCS / "kid" / f"{key}.html")

        out.append(
            {
                "key": key,
                "name": fund["name"],
                "isin": product.isin,
                "sfdr": fund["sfdr"],
                "rhp_years": fund["rhp_years"],
                "currency": product.currency,
                "objectives": OBJECTIVES[key],
                "sri": kid.sri.sri,
                "mrm_class": kid.sri.market.mrm_class,
                "crm_class": kid.sri.credit.crm_class,
                "vev": round(kid.sri.market.vev, 6),
                "volatility_annualised": round(kid.sri.market.volatility * (256**0.5), 6),
                "skewness": round(kid.sri.market.skewness, 4),
                "excess_kurtosis": round(kid.sri.market.excess_kurtosis, 4),
                "observations": kid.sri.market.observations,
                "scenarios": [s.to_dict() for s in kid.scenario_sets],
                "costs": [c.to_dict() for c in kid.cost_rows],
                "ongoing_charge": round(COSTS[key].ongoing * 100, 4),
                "kid_url": f"kid/{key}.html",
            }
        )
        print(f"  KID for {fund['name']}: SRI {kid.sri.sri}")
    return out


def build_ucits() -> dict:
    portfolio = load_portfolio(DATA / "sample_portfolio.csv")
    findings = check_portfolio(portfolio)
    exposure = commitment_approach_exposure(portfolio)

    write_findings_html(
        findings,
        DOCS / "reports" / "ucits.html",
        title=f"UCITS limit review: {portfolio.fund_name}",
        subtitle=(
            f"NAV {portfolio.nav:,.0f} {portfolio.base_currency} as at {portfolio.valuation_date}"
        ),
    )
    print(f"  UCITS review: {findings.summary_line()}")

    return {
        "fund_name": portfolio.fund_name,
        "nav": portfolio.nav,
        "currency": portfolio.base_currency,
        "valuation_date": portfolio.valuation_date,
        "positions": len(portfolio),
        "counts": findings.counts(),
        "compliant": findings.compliant,
        "global_exposure_pct": round(exposure.global_exposure_pct, 4),
        "gross_exposure_pct": round(exposure.gross_exposure_pct, 4),
        "netting_benefit_pct": round(exposure.netting_benefit_pct, 4),
        "report_url": "reports/ucits.html",
        "findings": [
            f.to_dict()
            for f in findings.sorted_by_severity()
            if f.severity.value in {"breach", "warning"}
        ],
    }


def build_templates() -> dict:
    eet_spec, emt_spec = load_eet_spec(), load_emt_spec()
    eet = validate_file(DATA / "sample_eet.csv", eet_spec)
    emt = validate_file(DATA / "sample_emt.csv", emt_spec)
    reconciliation = cross_template_findings(
        read_delimited(DATA / "sample_eet.csv"), read_delimited(DATA / "sample_emt.csv")
    )

    write_findings_html(
        eet.findings,
        DOCS / "reports" / "eet.html",
        title="European ESG Template validation",
        subtitle=f"{eet_spec.template} {eet_spec.version} - {eet.row_count} share classes",
    )
    write_findings_html(
        emt.findings,
        DOCS / "reports" / "emt.html",
        title="European MiFID Template validation",
        subtitle=f"{emt_spec.template} {emt_spec.version} - {emt.row_count} share classes",
    )
    write_findings_html(
        reconciliation,
        DOCS / "reports" / "reconciliation.html",
        title="EET / EMT reconciliation",
        subtitle="Sustainability preference fields compared across the distribution pack",
    )
    print(f"  EET: {eet.findings.summary_line()}")
    print(f"  EMT: {emt.findings.summary_line()}")
    print(f"  Reconciliation: {reconciliation.summary_line()}")

    def pack(result, url: str) -> dict:
        return {
            "template": result.spec.template,
            "version": result.spec.version,
            "rows": result.row_count,
            "fields": len(result.spec.fields),
            "rules": len(result.spec.rules),
            "counts": result.findings.counts(),
            "valid": result.valid,
            "report_url": url,
            "findings": [
                f.to_dict()
                for f in result.findings.sorted_by_severity()
                if f.severity.value in {"breach", "warning"}
            ],
        }

    return {
        "eet": pack(eet, "reports/eet.html"),
        "emt": pack(emt, "reports/emt.html"),
        "reconciliation": {
            "counts": reconciliation.counts(),
            "report_url": "reports/reconciliation.html",
            "findings": [
                f.to_dict()
                for f in reconciliation.sorted_by_severity()
                if f.severity.value in {"breach", "warning"}
            ],
        },
        "eet_rows": read_delimited(DATA / "sample_eet.csv"),
        "emt_rows": read_delimited(DATA / "sample_emt.csv"),
    }


def main() -> None:
    print("Building site into docs/")
    payload = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "version": __version__,
        "funds": build_kids(),
        "ucits": build_ucits(),
        "templates": build_templates(),
    }

    target = DOCS / "data" / "funds.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"  wrote {target.relative_to(ROOT)} ({target.stat().st_size / 1024:.0f} kB)")


if __name__ == "__main__":
    main()
