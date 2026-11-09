"""Command line interface.

::

    fundreg ucits   data/sample_portfolio.csv --html out/ucits.html
    fundreg priips  data/returns/meridian_european_equity.csv --rhp 5 --html out/kid.html
    fundreg eet     data/sample_eet.csv --html out/eet.html
    fundreg emt     data/sample_emt.csv
    fundreg reconcile data/sample_eet.csv data/sample_emt.csv
    fundreg aifmd   data/sample_portfolio.csv --aifm-aum 4.2e9 --xml out/annexiv.xml

Exit codes follow the convention a CI pipeline expects: ``0`` when nothing is
breached, ``1`` when something is, ``2`` on a usage or input error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fundreg import __version__
from fundreg.aifmd import AifInfo, AifmInfo, build_report, report_to_xml
from fundreg.findings import FindingSet, Severity
from fundreg.models import load_portfolio, load_returns
from fundreg.priips import CostStructure, ProductInfo, build_kid, write_html
from fundreg.templates import (
    TemplateSpec,
    cross_template_findings,
    load_eet_spec,
    load_emt_spec,
    read_delimited,
    validate_file,
    write_findings_html,
)

EXIT_OK, EXIT_BREACH, EXIT_ERROR = 0, 1, 2

_SYMBOL = {
    Severity.PASS: "pass",
    Severity.WARNING: "WARN",
    Severity.BREACH: "FAIL",
    Severity.INCONCLUSIVE: "n/a ",
}


def _print_findings(findings: FindingSet, *, show_passes: bool) -> None:
    rows = findings.sorted_by_severity()
    if not show_passes:
        rows = [f for f in rows if f.severity is not Severity.PASS]

    for f in rows:
        actual = "" if f.actual is None else f"{f.actual:>8.2f}"
        limit = "" if f.limit is None else f" / {f.limit:>6.2f}"
        print(f"  [{_SYMBOL[f.severity]}] {f.rule_id:<42}{actual}{limit}  {f.subject}")
        if f.severity is not Severity.PASS and f.message:
            print(f"         {f.message}")

    print()
    print(f"  {findings.summary_line()}")


def _emit(
    findings: FindingSet,
    args: argparse.Namespace,
    *,
    title: str,
    subtitle: str,
) -> int:
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(findings.to_json(), encoding="utf-8")
        print(f"  JSON written to {args.json}")
    if args.html:
        path = write_findings_html(findings, args.html, title=title, subtitle=subtitle)
        print(f"  Report written to {path}")
    return EXIT_BREACH if findings.breaches else EXIT_OK


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_ucits(args: argparse.Namespace) -> int:
    from fundreg.ucits import UcitsConfig, check_portfolio, commitment_approach_exposure

    portfolio = load_portfolio(args.portfolio)
    config = UcitsConfig(
        single_issuer_limit=args.single_issuer_limit,
        warning_ratio=args.warning_ratio,
    )
    findings = check_portfolio(portfolio, config)
    exposure = commitment_approach_exposure(portfolio)

    print(f"\nUCITS limit review: {portfolio.fund_name}")
    print(
        f"  NAV {portfolio.nav:,.0f} {portfolio.base_currency}"
        f"  valuation {portfolio.valuation_date or 'n/a'}"
        f"  {len(portfolio)} positions"
    )
    print(
        f"  Global exposure {exposure.global_exposure_pct:.2f}% of NAV "
        f"(gross {exposure.gross_exposure_pct:.2f}%, "
        f"netting benefit {exposure.netting_benefit_pct:.2f}%)\n"
    )
    _print_findings(findings, show_passes=args.all)
    return _emit(
        findings,
        args,
        title=f"UCITS limit review: {portfolio.fund_name}",
        subtitle=(
            f"NAV {portfolio.nav:,.0f} {portfolio.base_currency} as at "
            f"{portfolio.valuation_date or 'n/a'}"
        ),
    )


def cmd_priips(args: argparse.Namespace) -> int:
    returns = load_returns(args.returns)
    product = ProductInfo(
        name=args.name,
        isin=args.isin,
        manufacturer=args.manufacturer,
        rhp_years=args.rhp,
        currency=args.currency,
        objectives=args.objectives,
        intended_investor=args.investor,
    )
    costs = CostStructure(
        entry_cost=args.entry_cost / 100,
        exit_cost=args.exit_cost / 100,
        management_fee=args.management_fee / 100,
        transaction_cost=args.transaction_cost / 100,
        performance_fee=args.performance_fee / 100,
    )
    kid = build_kid(
        product,
        returns,
        costs,
        periods_per_year=args.periods_per_year,
        scenario_method=args.method,
    )

    sri = kid.sri
    print(f"\nPRIIPs KID: {product.name}")
    print(f"  Observations {sri.market.observations:,}  RHP {product.rhp_years:g} years")
    print(
        f"  SRI {sri.sri} of 7  (market risk class {sri.market.mrm_class}, "
        f"credit risk class {sri.credit.crm_class})"
    )
    print(
        f"  VaR-equivalent volatility {sri.market.vev:.2%}  "
        f"skewness {sri.market.skewness:+.3f}  excess kurtosis {sri.market.excess_kurtosis:+.3f}"
    )

    for sset in kid.scenario_sets:
        print(f"\n  Performance scenarios, exit after {sset.years:g} year(s)")
        print(f"    {sset.methodology}")
        for scenario in sset.rows():
            print(
                f"      {scenario.label:<13} {product.currency} {scenario.value:>10,.0f}"
                f"   {scenario.annualised_return:>8.2%} a year"
            )

    print("\n  Costs over time")
    for row in kid.cost_rows:
        print(
            f"      exit after {row.years:g} year(s): total costs "
            f"{product.currency} {row.total_costs:>8,.0f}"
            f"   reduction in yield {row.riy:.2%}"
        )

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(kid.to_dict(), indent=2), encoding="utf-8")
        print(f"\n  JSON written to {args.json}")
    if args.html:
        print(f"  KID written to {write_html(kid, args.html)}")
    print()
    return EXIT_OK


def _validate(args: argparse.Namespace, spec: TemplateSpec) -> int:
    result = validate_file(args.file, spec, delimiter=args.delimiter)
    print(f"\n{spec.title} validation ({spec.template} {spec.version})")
    print(f"  {result.row_count} row(s) from {result.source}\n")
    _print_findings(result.findings, show_passes=args.all)
    return _emit(
        result.findings,
        args,
        title=f"{spec.title} validation",
        subtitle=f"{spec.template} {spec.version} - {result.row_count} row(s) from {result.source}",
    )


def cmd_eet(args: argparse.Namespace) -> int:
    spec = TemplateSpec.load(args.spec) if args.spec else load_eet_spec()
    return _validate(args, spec)


def cmd_emt(args: argparse.Namespace) -> int:
    spec = TemplateSpec.load(args.spec) if args.spec else load_emt_spec()
    return _validate(args, spec)


def cmd_reconcile(args: argparse.Namespace) -> int:
    findings = cross_template_findings(read_delimited(args.eet), read_delimited(args.emt))
    print("\nEET / EMT reconciliation")
    print(f"  {args.eet}\n  {args.emt}\n")
    _print_findings(findings, show_passes=args.all)
    return _emit(
        findings,
        args,
        title="EET / EMT reconciliation",
        subtitle=f"{Path(args.eet).name} against {Path(args.emt).name}",
    )


def cmd_aifmd(args: argparse.Namespace) -> int:
    portfolio = load_portfolio(args.portfolio)
    report = build_report(
        portfolio,
        AifmInfo(
            name=args.aifm_name,
            national_code=args.aifm_code,
            reporting_member_state=args.member_state,
            lei=args.aifm_lei,
            total_aum=args.aifm_aum,
            is_authorised=not args.sub_threshold,
        ),
        AifInfo(
            name=args.aif_name or portfolio.fund_name,
            national_code=args.aif_code,
            domicile=args.domicile,
            predominant_type=args.aif_type,
            geographical_focus=args.focus,
            master_feeder_status=args.master_feeder,
            master_aif_name=args.master_name,
            inception_date=args.inception,
        ),
        filing_type=args.filing_type,
    )

    gross, commitment = report.leverage.gross, report.leverage.commitment
    print(f"\nAIFMD Annex IV: {report.aif.name}")
    print(
        f"  NAV {portfolio.nav:,.0f} {portfolio.base_currency}"
        f"   assets under management {report.aum:,.0f}"
    )
    print(
        f"  Reporting {report.obligation.frequency.value}, period "
        f"{report.record['ReportingPeriodType']} {report.record['ReportingPeriodYear']}"
        f"  ({report.obligation.reference})"
    )
    print(f"  {report.obligation.rationale}\n")

    print("  Leverage")
    for result in (gross, commitment):
        print(f"    {result.method:<11} {result.ratio:>7.4f}x   exposure {result.exposure:>14,.0f}")
        for component in result.components:
            print(f"        + {component.label:<38} {component.amount:>14,.0f}")
        for component in result.excluded:
            print(f"        - {component.label:<38} {component.amount:>14,.0f}")
    if report.leverage.ucits_global_exposure_pct is not None:
        print(
            f"    UCITS global exposure for comparison: "
            f"{report.leverage.ucits_global_exposure_pct:.2f}% of NAV "
            f"(derivatives only, a different measure)"
        )
    print(
        f"    Substantially leveraged (Art. 111): "
        f"{'yes' if report.substantially_leveraged else 'no'};"
        f" filing as content type {report.record['AIFContentType']}\n"
    )

    print("  Principal exposures after look-through")
    for index, (name, value) in enumerate(report.principal_exposures, start=1):
        print(f"    {index}. {name:<38} {value:>14,.0f}  {portfolio.pct(value):>6.2f}% of NAV")
    print()

    _print_findings(report.findings, show_passes=args.all)

    if args.xml:
        Path(args.xml).parent.mkdir(parents=True, exist_ok=True)
        Path(args.xml).write_text(report_to_xml(report), encoding="utf-8")
        print(f"  Annex IV XML written to {args.xml}")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        print(f"  JSON written to {args.json}")
    if args.html:
        path = write_findings_html(
            report.findings,
            args.html,
            title=f"AIFMD Annex IV validation: {report.aif.name}",
            subtitle=(
                f"Gross {gross.ratio:.4f}x, commitment {commitment.ratio:.4f}x, "
                f"{report.obligation.frequency.value} reporting"
            ),
        )
        print(f"  Report written to {path}")

    return EXIT_BREACH if report.findings.breaches else EXIT_OK


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--html", metavar="PATH", help="write an HTML report")
    parser.add_argument("--json", metavar="PATH", help="write findings as JSON")
    parser.add_argument(
        "--all", action="store_true", help="show rules that passed as well as those that did not"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fundreg",
        description="Regulatory calculations for the European fund industry.",
    )
    parser.add_argument("--version", action="version", version=f"fundreg {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    ucits = sub.add_parser("ucits", help="run the UCITS investment limit suite")
    ucits.add_argument("portfolio", help="portfolio CSV")
    ucits.add_argument(
        "--single-issuer-limit",
        type=float,
        default=10.0,
        dest="single_issuer_limit",
        help="Art. 52 single issuer limit in %% of NAV (5 or 10, default 10)",
    )
    ucits.add_argument(
        "--warning-ratio",
        type=float,
        default=0.9,
        dest="warning_ratio",
        help="fraction of a limit at which to raise an early warning (default 0.9)",
    )
    _add_output_options(ucits)
    ucits.set_defaults(func=cmd_ucits)

    priips = sub.add_parser("priips", help="compute a PRIIPs KID")
    priips.add_argument("returns", help="CSV or JSON of periodic returns")
    priips.add_argument(
        "--rhp", type=float, default=5.0, help="recommended holding period in years"
    )
    priips.add_argument("--name", default="Sample Fund", help="product name")
    priips.add_argument("--isin", default="", help="share class ISIN")
    priips.add_argument("--manufacturer", default="Sample Management Company")
    priips.add_argument("--currency", default="EUR")
    priips.add_argument("--objectives", default="", help="objectives paragraph for the KID")
    priips.add_argument("--investor", default="", help="intended retail investor paragraph")
    priips.add_argument(
        "--periods-per-year",
        type=int,
        default=256,
        dest="periods_per_year",
        help="256 daily, 52 weekly, 12 monthly (default 256)",
    )
    priips.add_argument(
        "--method",
        choices=("historical", "cornish_fisher"),
        default="historical",
        help="performance scenario methodology (default historical, per (EU) 2021/2268)",
    )
    for flag, default, helptext in (
        ("entry-cost", 0.0, "one-off entry cost in %%"),
        ("exit-cost", 0.0, "one-off exit cost in %%"),
        ("management-fee", 1.0, "annual management fee in %%"),
        ("transaction-cost", 0.15, "annual transaction cost in %%"),
        ("performance-fee", 0.0, "annual performance fee in %%"),
    ):
        priips.add_argument(
            f"--{flag}", type=float, default=default, dest=flag.replace("-", "_"), help=helptext
        )
    priips.add_argument("--html", metavar="PATH", help="write the KID as HTML")
    priips.add_argument("--json", metavar="PATH", help="write the KID data as JSON")
    priips.set_defaults(func=cmd_priips)

    for name, helptext, handler in (
        ("eet", "validate a European ESG Template", cmd_eet),
        ("emt", "validate a European MiFID Template", cmd_emt),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("file", help="template file (CSV, comma or semicolon delimited)")
        p.add_argument("--spec", help="path to an alternative JSON spec")
        p.add_argument("--delimiter", help="force a delimiter instead of sniffing it")
        _add_output_options(p)
        p.set_defaults(func=handler)

    rec = sub.add_parser("reconcile", help="reconcile an EET against an EMT")
    rec.add_argument("eet", help="EET file")
    rec.add_argument("emt", help="EMT file")
    _add_output_options(rec)
    rec.set_defaults(func=cmd_reconcile)

    aifmd = sub.add_parser("aifmd", help="build an AIFMD Annex IV AIF report")
    aifmd.add_argument("portfolio", help="portfolio CSV")
    aifmd.add_argument(
        "--aifm-aum",
        type=float,
        default=0.0,
        dest="aifm_aum",
        help="total assets under management of the AIFM in euro, which drives the "
        "Art. 110 reporting frequency",
    )
    aifmd.add_argument("--aifm-name", default="Sample Management Company", dest="aifm_name")
    aifmd.add_argument("--aifm-code", default="AIFM-0001", dest="aifm_code")
    aifmd.add_argument("--aifm-lei", default="", dest="aifm_lei")
    aifmd.add_argument("--aif-name", default="", dest="aif_name")
    aifmd.add_argument("--aif-code", default="AIF-0001", dest="aif_code")
    aifmd.add_argument("--member-state", default="LU", dest="member_state")
    aifmd.add_argument("--domicile", default="LU")
    aifmd.add_argument("--inception", default="", help="inception date, YYYY-MM-DD")
    aifmd.add_argument(
        "--aif-type",
        default="OTHR",
        dest="aif_type",
        choices=("HF", "PE", "RE", "FOF", "OTHR", "NONE"),
        help="predominant AIF type",
    )
    aifmd.add_argument(
        "--focus",
        default="EEA",
        choices=("EEA", "EUROPE_NON_EEA", "NORTH_AMERICA", "ASIA_PACIFIC", "GLOBAL", "OTHER"),
        help="geographical focus",
    )
    aifmd.add_argument(
        "--master-feeder",
        default="NONE",
        dest="master_feeder",
        choices=("NONE", "FEEDER", "MASTER"),
    )
    aifmd.add_argument("--master-name", default="", dest="master_name")
    aifmd.add_argument(
        "--filing-type",
        default="INIT",
        dest="filing_type",
        choices=("INIT", "AMND", "CANC"),
    )
    aifmd.add_argument(
        "--sub-threshold",
        action="store_true",
        dest="sub_threshold",
        help="the AIFM is registered under Art. 3(2) rather than authorised",
    )
    aifmd.add_argument("--xml", metavar="PATH", help="write the Annex IV XML")
    _add_output_options(aifmd)
    aifmd.set_defaults(func=cmd_aifmd)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
