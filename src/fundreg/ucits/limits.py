"""UCITS investment limit tests (Directive 2009/65/EC).

Every rule returns a :class:`~fundreg.findings.Finding` whether or not it is
breached, so the output doubles as evidence that a rule was evaluated. That is
what a depositary or an internal-audit reviewer actually asks for: not "were
there breaches" but "which limits did you test, against what numbers".

Rules implemented
-----------------
======================================  ==================================================
Rule id                                 Limit
======================================  ==================================================
``UCITS.ART50.2A.INELIGIBLE``           10% in assets outside the Art. 50(1) eligible list
``UCITS.ART52.1A.SINGLE_ISSUER``        5% per issuer, raised to 10% where permitted
``UCITS.ART52.2.AGGREGATE_40``          40% aggregate of issuer positions above 5%
``UCITS.ART52.1B.DEPOSIT``              20% of deposits with a single body
``UCITS.ART52.1.OTC_COUNTERPARTY``      10% credit institution / 5% other counterparty
``UCITS.ART52.2.COMBINED_20``           20% combined securities + deposits + OTC per body
``UCITS.ART52.3.PUBLIC_BODY``           35% per sovereign or public issuer
``UCITS.ART52.4.COVERED_BOND``          25% per covered bond issuer, 80% in aggregate
``UCITS.ART55.1.SINGLE_CIS``            20% in a single UCITS or other CIS
``UCITS.ART55.1.NON_UCITS_AGGREGATE``   30% in non-UCITS collective investment undertakings
``UCITS.ART56.INFLUENCE``               10% of an issuer's securities, 25% of a CIS
``UCITS.ART51.3.GLOBAL_EXPOSURE``       100% of NAV of incremental derivative exposure
``UCITS.ART83.BORROWING``               10% temporary borrowing
======================================  ==================================================
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from fundreg.findings import Finding, FindingSet, Severity
from fundreg.models import (
    DERIVATIVE_CLASSES,
    ISSUER_LIMIT_CLASSES,
    AssetClass,
    Portfolio,
)
from fundreg.ucits.exposure import commitment_approach_exposure, counterparty_exposures

DIRECTIVE = "Directive 2009/65/EC"


@dataclass
class UcitsConfig:
    """Fund-specific choices within the limits the Directive allows.

    Several UCITS limits are options exercised by the home Member State or
    written into the prospectus, so a compliance engine that hard-codes them is
    wrong for most funds.
    """

    #: Art. 52(2): Member States may raise the 5% single-issuer limit to 10%.
    single_issuer_limit: float = 10.0
    #: Art. 52(2): aggregate of positions above the trigger.
    aggregate_40_limit: float = 40.0
    aggregate_40_trigger: float = 5.0
    deposit_limit: float = 20.0
    otc_credit_institution_limit: float = 10.0
    otc_other_counterparty_limit: float = 5.0
    combined_issuer_limit: float = 20.0
    public_body_limit: float = 35.0
    covered_bond_limit: float = 25.0
    covered_bond_aggregate_limit: float = 80.0
    covered_bond_aggregate_trigger: float = 5.0
    single_cis_limit: float = 20.0
    non_ucits_aggregate_limit: float = 30.0
    ineligible_asset_limit: float = 10.0
    global_exposure_limit: float = 100.0
    borrowing_limit: float = 10.0
    influence_security_limit: float = 10.0
    influence_cis_limit: float = 25.0
    #: Fraction of a limit at which an early warning is raised. 0.9 means a
    #: position at 90% of the limit is flagged before it breaches.
    warning_ratio: float = 0.9
    #: Counterparties known to be credit institutions subject to prudential
    #: supervision, which attract the 10% rather than the 5% limit. Leave empty
    #: to treat every OTC counterparty as a credit institution and say so in the
    #: finding detail.
    credit_institutions: set[str] = field(default_factory=set)
    #: Art. 52(3) permits a UCITS to invest up to 100% in a single public issuer
    #: where the prospectus discloses it and at least six issues are held.
    art52_4_derogation: bool = False


def _evaluate(
    *,
    rule_id: str,
    reference: str,
    title: str,
    subject: str,
    actual: float,
    limit: float,
    config: UcitsConfig,
    message: str = "",
    detail: dict | None = None,
    unit: str = "% NAV",
) -> Finding:
    """Grade an observed value against a limit with an early-warning band."""

    if actual > limit + 1e-9:
        severity = Severity.BREACH
        verdict = f"{actual:.2f}{unit.replace('% NAV', '%')} exceeds the {limit:.2f}% limit"
    elif actual >= limit * config.warning_ratio:
        severity = Severity.WARNING
        verdict = f"{actual:.2f}% is within {limit - actual:.2f} points of the {limit:.2f}% limit"
    else:
        severity = Severity.PASS
        verdict = f"{actual:.2f}% against a {limit:.2f}% limit"

    return Finding(
        rule_id=rule_id,
        reference=reference,
        title=title,
        severity=severity,
        subject=subject,
        actual=round(actual, 6),
        limit=limit,
        unit=unit,
        message=message or verdict,
        detail=detail or {},
    )


def check_portfolio(portfolio: Portfolio, config: UcitsConfig | None = None) -> FindingSet:
    """Run the full UCITS limit suite over a portfolio."""

    config = config or UcitsConfig()
    if portfolio.nav <= 0:
        raise ValueError("NAV must be positive")

    findings = FindingSet()
    _check_eligibility(portfolio, config, findings)
    _check_issuer_concentration(portfolio, config, findings)
    _check_deposits(portfolio, config, findings)
    _check_otc_counterparty(portfolio, config, findings)
    _check_combined_issuer(portfolio, config, findings)
    _check_collective_investments(portfolio, config, findings)
    _check_influence(portfolio, config, findings)
    _check_global_exposure(portfolio, config, findings)
    _check_borrowing(portfolio, config, findings)
    return findings


# --------------------------------------------------------------------------- #
# Art. 50(2)(a) - the "trash ratio"
# --------------------------------------------------------------------------- #


def _check_eligibility(portfolio: Portfolio, config: UcitsConfig, findings: FindingSet) -> None:
    ineligible = [
        p
        for p in portfolio
        if p.asset_class is AssetClass.INELIGIBLE or not p.instrument.is_eligible_art50
    ]
    value = sum(abs(p.market_value) for p in ineligible)
    findings.add(
        _evaluate(
            rule_id="UCITS.ART50.2A.INELIGIBLE",
            reference=f"{DIRECTIVE} Art. 50(2)(a)",
            title="Assets outside the Art. 50(1) eligible list",
            subject=portfolio.fund_name,
            actual=portfolio.pct(value),
            limit=config.ineligible_asset_limit,
            config=config,
            detail={"positions": [p.instrument.instrument_id for p in ineligible]},
        )
    )


# --------------------------------------------------------------------------- #
# Art. 52(1)-(5) - issuer concentration
# --------------------------------------------------------------------------- #


def _issuer_security_exposure(portfolio: Portfolio) -> dict[str, dict]:
    """Aggregate transferable securities and money market instruments per issuer.

    Derivatives are looked through to the issuer of their underlying, as
    required by Art. 51(3) second subparagraph: exposure to the underlying
    counts towards the Art. 52 issuer limits.
    """

    buckets: dict[str, dict] = defaultdict(
        lambda: {
            "value": 0.0,
            "name": "",
            "is_public_body": False,
            "is_covered_bond": False,
            "instruments": [],
        }
    )

    for position in portfolio:
        instrument = position.instrument
        if instrument.asset_class in ISSUER_LIMIT_CLASSES:
            value = position.market_value
        elif instrument.asset_class in DERIVATIVE_CLASSES:
            if not instrument.look_through_to_issuer:
                # Index, basket, rate and currency derivatives have no single
                # obligor to attribute the exposure to.
                continue
            derivative = instrument.derivative
            assert derivative is not None
            # Look through to the underlying issuer exposure.
            value = derivative.notional
        else:
            continue

        bucket = buckets[instrument.issuer_id]
        bucket["value"] += value
        bucket["name"] = instrument.issuer_name or instrument.issuer_id
        bucket["is_public_body"] = bucket["is_public_body"] or instrument.is_public_body
        bucket["is_covered_bond"] = bucket["is_covered_bond"] or instrument.is_covered_bond
        bucket["instruments"].append(instrument.instrument_id)

    return dict(buckets)


def _check_issuer_concentration(
    portfolio: Portfolio, config: UcitsConfig, findings: FindingSet
) -> None:
    buckets = _issuer_security_exposure(portfolio)
    above_trigger: list[tuple[str, float]] = []
    covered_above_trigger: list[tuple[str, float]] = []

    for issuer_id, bucket in sorted(buckets.items(), key=lambda kv: -abs(kv[1]["value"])):
        pct = portfolio.pct(abs(bucket["value"]))
        subject = f"{bucket['name']} ({issuer_id})"
        detail = {"instruments": bucket["instruments"]}

        if bucket["is_public_body"]:
            limit = 100.0 if config.art52_4_derogation else config.public_body_limit
            findings.add(
                _evaluate(
                    rule_id="UCITS.ART52.3.PUBLIC_BODY",
                    reference=f"{DIRECTIVE} Art. 52(3)",
                    title="Single public-body issuer",
                    subject=subject,
                    actual=pct,
                    limit=limit,
                    config=config,
                    detail=detail | {"derogation_applied": config.art52_4_derogation},
                )
            )
            # Art. 52(5): Art. 52(3) and 52(4) holdings are excluded from the 40% test.
            continue

        if bucket["is_covered_bond"]:
            findings.add(
                _evaluate(
                    rule_id="UCITS.ART52.4.COVERED_BOND",
                    reference=f"{DIRECTIVE} Art. 52(4)",
                    title="Single covered bond issuer",
                    subject=subject,
                    actual=pct,
                    limit=config.covered_bond_limit,
                    config=config,
                    detail=detail,
                )
            )
            if pct > config.covered_bond_aggregate_trigger:
                covered_above_trigger.append((subject, pct))
            continue

        findings.add(
            _evaluate(
                rule_id="UCITS.ART52.1A.SINGLE_ISSUER",
                reference=f"{DIRECTIVE} Art. 52(1)(a), Art. 52(2)",
                title="Single issuer, transferable securities and money market instruments",
                subject=subject,
                actual=pct,
                limit=config.single_issuer_limit,
                config=config,
                detail=detail,
            )
        )
        if pct > config.aggregate_40_trigger:
            above_trigger.append((subject, pct))

    aggregate = sum(pct for _, pct in above_trigger)
    findings.add(
        _evaluate(
            rule_id="UCITS.ART52.2.AGGREGATE_40",
            reference=f"{DIRECTIVE} Art. 52(2), Art. 52(5)",
            title=f"Aggregate of issuers above {config.aggregate_40_trigger:.0f}% of NAV",
            subject=portfolio.fund_name,
            actual=aggregate,
            limit=config.aggregate_40_limit,
            config=config,
            detail={
                "contributing_issuers": [
                    {"issuer": s, "pct": round(p, 4)} for s, p in above_trigger
                ],
                "note": (
                    "Art. 52(3) public-body and Art. 52(4) covered bond holdings are "
                    "excluded by Art. 52(5), as are deposits and OTC derivatives."
                ),
            },
        )
    )

    covered_aggregate = sum(pct for _, pct in covered_above_trigger)
    findings.add(
        _evaluate(
            rule_id="UCITS.ART52.4.COVERED_BOND_AGGREGATE",
            reference=f"{DIRECTIVE} Art. 52(4)",
            title=f"Aggregate of covered bond issuers above {config.covered_bond_aggregate_trigger:.0f}% of NAV",
            subject=portfolio.fund_name,
            actual=covered_aggregate,
            limit=config.covered_bond_aggregate_limit,
            config=config,
            detail={
                "contributing_issuers": [
                    {"issuer": s, "pct": round(p, 4)} for s, p in covered_above_trigger
                ]
            },
        )
    )


def _check_deposits(portfolio: Portfolio, config: UcitsConfig, findings: FindingSet) -> None:
    per_body: dict[str, float] = defaultdict(float)
    names: dict[str, str] = {}
    for position in portfolio.of_class(AssetClass.DEPOSIT):
        per_body[position.issuer_id] += position.market_value
        names[position.issuer_id] = position.instrument.issuer_name or position.issuer_id

    for body, value in sorted(per_body.items(), key=lambda kv: -kv[1]):
        findings.add(
            _evaluate(
                rule_id="UCITS.ART52.1B.DEPOSIT",
                reference=f"{DIRECTIVE} Art. 52(1)(b)",
                title="Deposits with a single body",
                subject=f"{names[body]} ({body})",
                actual=portfolio.pct(value),
                limit=config.deposit_limit,
                config=config,
            )
        )


def _check_otc_counterparty(
    portfolio: Portfolio, config: UcitsConfig, findings: FindingSet
) -> None:
    exposures = counterparty_exposures(portfolio)
    for counterparty, value in exposures.items():
        if config.credit_institutions:
            is_credit_institution = counterparty in config.credit_institutions
            assumed = False
        else:
            is_credit_institution = True
            assumed = True
        limit = (
            config.otc_credit_institution_limit
            if is_credit_institution
            else config.otc_other_counterparty_limit
        )
        findings.add(
            _evaluate(
                rule_id="UCITS.ART52.1.OTC_COUNTERPARTY",
                reference=f"{DIRECTIVE} Art. 52(1), fourth indent",
                title="OTC derivative counterparty risk exposure",
                subject=counterparty,
                actual=portfolio.pct(value),
                limit=limit,
                config=config,
                detail={
                    "credit_institution": is_credit_institution,
                    "assumed_credit_institution": assumed,
                    "note": (
                        "No credit-institution list configured, so the 10% limit was "
                        "applied. Configure UcitsConfig.credit_institutions to apply "
                        "the 5% limit to other counterparties."
                    )
                    if assumed
                    else "",
                },
            )
        )


def _check_combined_issuer(portfolio: Portfolio, config: UcitsConfig, findings: FindingSet) -> None:
    """Art. 52(2) second subparagraph: 20% combined exposure to a single body."""

    combined: dict[str, float] = defaultdict(float)
    names: dict[str, str] = {}

    for issuer_id, bucket in _issuer_security_exposure(portfolio).items():
        if bucket["is_public_body"] or bucket["is_covered_bond"]:
            continue
        combined[issuer_id] += abs(bucket["value"])
        names[issuer_id] = bucket["name"]

    for position in portfolio.of_class(AssetClass.DEPOSIT):
        combined[position.issuer_id] += position.market_value
        names.setdefault(position.issuer_id, position.instrument.issuer_name or position.issuer_id)

    for counterparty, value in counterparty_exposures(portfolio).items():
        combined[counterparty] += value
        names.setdefault(counterparty, counterparty)

    for issuer_id, value in sorted(combined.items(), key=lambda kv: -kv[1]):
        findings.add(
            _evaluate(
                rule_id="UCITS.ART52.2.COMBINED_20",
                reference=f"{DIRECTIVE} Art. 52(2), second subparagraph",
                title="Combined securities, deposits and OTC exposure to a single body",
                subject=f"{names.get(issuer_id, issuer_id)} ({issuer_id})",
                actual=portfolio.pct(value),
                limit=config.combined_issuer_limit,
                config=config,
            )
        )


# --------------------------------------------------------------------------- #
# Art. 55 - investment in other collective investment undertakings
# --------------------------------------------------------------------------- #


def _check_collective_investments(
    portfolio: Portfolio, config: UcitsConfig, findings: FindingSet
) -> None:
    per_fund: dict[str, float] = defaultdict(float)
    names: dict[str, str] = {}
    non_ucits_total = 0.0

    for position in portfolio.of_class(AssetClass.UCITS_FUND, AssetClass.NON_UCITS_CIS):
        per_fund[position.instrument.instrument_id] += position.market_value
        names[position.instrument.instrument_id] = position.instrument.name
        if position.asset_class is AssetClass.NON_UCITS_CIS:
            non_ucits_total += position.market_value

    for fund_id, value in sorted(per_fund.items(), key=lambda kv: -kv[1]):
        findings.add(
            _evaluate(
                rule_id="UCITS.ART55.1.SINGLE_CIS",
                reference=f"{DIRECTIVE} Art. 55(1)",
                title="Units of a single UCITS or other collective investment undertaking",
                subject=f"{names[fund_id]} ({fund_id})",
                actual=portfolio.pct(value),
                limit=config.single_cis_limit,
                config=config,
            )
        )

    findings.add(
        _evaluate(
            rule_id="UCITS.ART55.1.NON_UCITS_AGGREGATE",
            reference=f"{DIRECTIVE} Art. 55(1), second subparagraph",
            title="Aggregate investment in non-UCITS collective investment undertakings",
            subject=portfolio.fund_name,
            actual=portfolio.pct(non_ucits_total),
            limit=config.non_ucits_aggregate_limit,
            config=config,
        )
    )


# --------------------------------------------------------------------------- #
# Art. 56 - limits on significant influence
# --------------------------------------------------------------------------- #


def _check_influence(portfolio: Portfolio, config: UcitsConfig, findings: FindingSet) -> None:
    """Art. 56(1)-(2): limits expressed as a share of the issue, not of NAV."""

    per_instrument: dict[str, float] = defaultdict(float)
    issued: dict[str, float | None] = {}
    meta: dict[str, tuple[str, AssetClass]] = {}

    for position in portfolio:
        instrument = position.instrument
        if instrument.asset_class in DERIVATIVE_CLASSES:
            continue
        per_instrument[instrument.instrument_id] += position.quantity
        issued[instrument.instrument_id] = instrument.issuer_total_units
        meta[instrument.instrument_id] = (instrument.name, instrument.asset_class)

    for instrument_id, held in sorted(per_instrument.items()):
        total = issued.get(instrument_id)
        name, asset_class = meta[instrument_id]
        if asset_class in (AssetClass.UCITS_FUND, AssetClass.NON_UCITS_CIS):
            limit = config.influence_cis_limit
            title = "Share of the units of a single collective investment undertaking"
            reference = f"{DIRECTIVE} Art. 56(2)(d)"
        elif asset_class in ISSUER_LIMIT_CLASSES:
            limit = config.influence_security_limit
            title = "Share of a single issue held"
            reference = f"{DIRECTIVE} Art. 56(2)"
        else:
            continue

        if not total:
            findings.add(
                Finding(
                    rule_id="UCITS.ART56.INFLUENCE",
                    reference=reference,
                    title=title,
                    severity=Severity.INCONCLUSIVE,
                    subject=f"{name} ({instrument_id})",
                    limit=limit,
                    unit="% of issue",
                    message=(
                        "Total units in issue not supplied, so the Art. 56 influence "
                        "limit could not be evaluated."
                    ),
                )
            )
            continue

        findings.add(
            _evaluate(
                rule_id="UCITS.ART56.INFLUENCE",
                reference=reference,
                title=title,
                subject=f"{name} ({instrument_id})",
                actual=100.0 * held / total,
                limit=limit,
                config=config,
                unit="% of issue",
                detail={"units_held": held, "units_in_issue": total},
            )
        )


# --------------------------------------------------------------------------- #
# Art. 51(3) global exposure and Art. 83 borrowing
# --------------------------------------------------------------------------- #


def _check_global_exposure(portfolio: Portfolio, config: UcitsConfig, findings: FindingSet) -> None:
    result = commitment_approach_exposure(portfolio)
    findings.add(
        _evaluate(
            rule_id="UCITS.ART51.3.GLOBAL_EXPOSURE",
            reference=f"{DIRECTIVE} Art. 51(3); CESR/10-788",
            title="Global exposure, commitment approach",
            subject=portfolio.fund_name,
            actual=result.global_exposure_pct,
            limit=config.global_exposure_limit,
            config=config,
            detail={
                "gross_exposure_pct": round(result.gross_exposure_pct, 4),
                "netting_benefit_pct": round(result.netting_benefit_pct, 4),
                "total_exposure_pct": round(result.total_exposure_pct, 4),
                "netting_groups": [
                    {
                        "netting_set": g.netting_set,
                        "underlying": g.underlying_id,
                        "gross": round(g.gross, 2),
                        "net": round(g.net, 2),
                        "contracts": g.contracts,
                    }
                    for g in result.groups
                ],
            },
        )
    )


def _check_borrowing(portfolio: Portfolio, config: UcitsConfig, findings: FindingSet) -> None:
    findings.add(
        _evaluate(
            rule_id="UCITS.ART83.BORROWING",
            reference=f"{DIRECTIVE} Art. 83(2)(b)",
            title="Temporary borrowing",
            subject=portfolio.fund_name,
            actual=portfolio.pct(portfolio.borrowing),
            limit=config.borrowing_limit,
            config=config,
        )
    )
