"""Validate EET and EMT files against a :class:`~fundreg.templates.spec.TemplateSpec`.

Three layers of checking, in the order a data-operations team hits them:

1. **Structure** - unknown columns, missing columns, duplicate keys.
2. **Field level** - presence of mandatory and conditionally mandatory fields,
   data types, value domains, numeric bounds, ISIN and LEI formats.
3. **Cross field** - the consistency rules that make a template meaningful, such
   as a taxonomy-aligned share that exceeds the sustainable-investment share it
   is supposed to be a subset of.

A fourth layer, :func:`cross_template_findings`, reconciles an EET row against
the EMT row for the same share class, which is where most real breaks show up:
the two files are produced by different teams on different cycles.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fundreg.findings import Finding, FindingSet, Severity
from fundreg.templates.spec import (
    Requirement,
    TemplateSpec,
    check_value,
    describe,
    evaluate,
)

_SEVERITY_FROM_SPEC = {
    "breach": Severity.BREACH,
    "error": Severity.BREACH,
    "warning": Severity.WARNING,
    "info": Severity.PASS,
}


@dataclass
class ValidationResult:
    """Outcome of validating one file."""

    spec: TemplateSpec
    source: str
    row_count: int
    findings: FindingSet
    records: list[dict[str, str]]

    @property
    def valid(self) -> bool:
        return self.findings.compliant

    def summary(self) -> dict[str, Any]:
        counts = self.findings.counts()
        return {
            "template": self.spec.template,
            "version": self.spec.version,
            "source": self.source,
            "rows": self.row_count,
            "valid": self.valid,
            **counts,
        }


def read_delimited(path: str | Path, *, delimiter: str | None = None) -> list[dict[str, str]]:
    """Read a CSV or semicolon-delimited file into a list of records.

    Template files circulate in both comma and semicolon flavours depending on
    the locale of whoever exported them, so the delimiter is sniffed.
    """

    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if delimiter is None:
        head = text.split("\n", 1)[0]
        delimiter = ";" if head.count(";") > head.count(",") else ","
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]


def validate_records(
    records: Sequence[Mapping[str, str]],
    spec: TemplateSpec,
    *,
    source: str = "(in memory)",
) -> ValidationResult:
    """Run every layer of validation over already-parsed records."""

    findings = FindingSet()
    materialised = [dict(r) for r in records]

    _check_structure(materialised, spec, findings)
    for index, record in enumerate(materialised, start=1):
        key = _row_key(record, spec, index)
        _check_fields(record, spec, findings, key)
        _check_rules(record, spec, findings, key)

    return ValidationResult(
        spec=spec,
        source=source,
        row_count=len(materialised),
        findings=findings,
        records=materialised,
    )


def validate_file(
    path: str | Path, spec: TemplateSpec, *, delimiter: str | None = None
) -> ValidationResult:
    """Read and validate a template file."""

    records = read_delimited(path, delimiter=delimiter)
    return validate_records(records, spec, source=str(path))


# --------------------------------------------------------------------------- #
# Layer 1: structure
# --------------------------------------------------------------------------- #


def _row_key(record: Mapping[str, str], spec: TemplateSpec, index: int) -> str:
    value = record.get(spec.key_field, "") if spec.key_field else ""
    return value or f"row {index}"


def _check_structure(
    records: Sequence[Mapping[str, str]], spec: TemplateSpec, findings: FindingSet
) -> None:
    declared = set(spec.by_code)
    present: set[str] = set()
    for record in records:
        present.update(record.keys())

    missing = sorted(
        code
        for code in declared
        if code not in present and spec.by_code[code].requirement is Requirement.MANDATORY
    )
    if missing:
        findings.add(
            Finding(
                rule_id=f"{spec.template}.STRUCTURE.MISSING_COLUMN",
                reference=f"{spec.template} {spec.version}",
                title="Mandatory column absent from the file",
                severity=Severity.BREACH,
                subject=spec.template,
                message=f"{len(missing)} mandatory column(s) are not present: {', '.join(missing)}",
                detail={"columns": missing},
            )
        )

    unknown = sorted(code for code in present if code and code not in declared)
    if unknown:
        findings.add(
            Finding(
                rule_id=f"{spec.template}.STRUCTURE.UNKNOWN_COLUMN",
                reference=f"{spec.template} {spec.version}",
                title="Column not recognised by the template version",
                severity=Severity.WARNING,
                subject=spec.template,
                message=(
                    f"{len(unknown)} column(s) are not part of this template version and were "
                    f"ignored: {', '.join(unknown)}"
                ),
                detail={"columns": unknown},
            )
        )

    if spec.key_field:
        seen: dict[str, int] = {}
        duplicates: list[str] = []
        for record in records:
            key = record.get(spec.key_field, "")
            if not key:
                continue
            seen[key] = seen.get(key, 0) + 1
            if seen[key] == 2:
                duplicates.append(key)
        if duplicates:
            findings.add(
                Finding(
                    rule_id=f"{spec.template}.STRUCTURE.DUPLICATE_KEY",
                    reference=f"{spec.template} {spec.version}",
                    title="Duplicate share class in the file",
                    severity=Severity.BREACH,
                    subject=spec.template,
                    message=(
                        f"{len(duplicates)} identifier(s) appear more than once: "
                        f"{', '.join(duplicates)}"
                    ),
                    detail={"identifiers": duplicates},
                )
            )


# --------------------------------------------------------------------------- #
# Layer 2: field level
# --------------------------------------------------------------------------- #


def _check_fields(
    record: Mapping[str, str], spec: TemplateSpec, findings: FindingSet, key: str
) -> None:
    for field_spec in spec.fields:
        raw = record.get(field_spec.code, "")
        populated = str(raw).strip() != ""

        if not populated:
            if field_spec.requirement is Requirement.MANDATORY:
                findings.add(
                    Finding(
                        rule_id=f"{spec.template}.FIELD.MANDATORY",
                        reference=field_spec.reference or f"{spec.template} {spec.version}",
                        title=f"{field_spec.code} is mandatory",
                        severity=Severity.BREACH,
                        subject=f"{key} / {field_spec.code}",
                        message=f"{field_spec.name} must be populated.",
                        detail={"section": field_spec.section},
                    )
                )
            elif field_spec.requirement is Requirement.CONDITIONAL and evaluate(
                field_spec.required_when, record
            ):
                findings.add(
                    Finding(
                        rule_id=f"{spec.template}.FIELD.CONDITIONAL",
                        reference=field_spec.reference or f"{spec.template} {spec.version}",
                        title=f"{field_spec.code} is required in this case",
                        severity=Severity.BREACH,
                        subject=f"{key} / {field_spec.code}",
                        message=(
                            f"{field_spec.name} becomes mandatory when "
                            f"{describe(field_spec.required_when)}."
                        ),
                        detail={
                            "section": field_spec.section,
                            "condition": field_spec.required_when,
                        },
                    )
                )
            continue

        error = check_value(field_spec, raw)
        if error:
            findings.add(
                Finding(
                    rule_id=f"{spec.template}.FIELD.VALUE",
                    reference=field_spec.reference or f"{spec.template} {spec.version}",
                    title=f"{field_spec.code} holds an invalid value",
                    severity=Severity.BREACH,
                    subject=f"{key} / {field_spec.code}",
                    message=f"{field_spec.name}: {error}.",
                    detail={"section": field_spec.section, "value": raw},
                )
            )


# --------------------------------------------------------------------------- #
# Layer 3: cross-field rules
# --------------------------------------------------------------------------- #


def _check_rules(
    record: Mapping[str, str], spec: TemplateSpec, findings: FindingSet, key: str
) -> None:
    for rule in spec.rules:
        if not evaluate(rule.when, record):
            continue
        if evaluate(rule.then, record):
            findings.add(
                Finding(
                    rule_id=rule.rule_id,
                    reference=rule.reference,
                    title=rule.title,
                    severity=Severity.PASS,
                    subject=key,
                    message="Condition satisfied.",
                )
            )
            continue
        findings.add(
            Finding(
                rule_id=rule.rule_id,
                reference=rule.reference,
                title=rule.title,
                severity=_SEVERITY_FROM_SPEC.get(rule.severity, Severity.BREACH),
                subject=key,
                message=rule.message or f"Expected {describe(rule.then)}.",
                detail={
                    "expected": describe(rule.then),
                    "applies_when": describe(rule.when),
                    "values": {
                        code: record.get(code, "")
                        for code in _fields_in(rule.when) | _fields_in(rule.then)
                    },
                },
            )
        )


def _fields_in(condition: Mapping[str, Any] | None) -> set[str]:
    """Collect every field code a condition touches, for the evidence trail."""

    if not condition:
        return set()
    out: set[str] = set()
    for connective in ("all", "any"):
        if connective in condition:
            for child in condition[connective]:
                out |= _fields_in(child)
            return out
    if "not" in condition:
        return _fields_in(condition["not"])
    if "field" in condition:
        out.add(condition["field"])
    for operator in (
        "gte_field",
        "lte_field",
        "after_field",
        "on_or_after_field",
        "before_field",
        "on_or_before_field",
    ):
        if operator in condition:
            out.add(condition[operator])
    return out


# --------------------------------------------------------------------------- #
# Layer 4: EET / EMT reconciliation
# --------------------------------------------------------------------------- #

#: EET field, EMT field, and how the two must relate.
CROSS_TEMPLATE_CHECKS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "21010_Preference_A_Taxonomy_Share",
        "10010_Preference_A_Taxonomy_Share",
        "equal",
        "Sustainability preference A differs between the EET and the EMT",
        "Delegated Regulation (EU) 2021/1253 Art. 2(7)(a)",
    ),
    (
        "21020_Preference_B_SFDR_Share",
        "10020_Preference_B_SFDR_Share",
        "equal",
        "Sustainability preference B differs between the EET and the EMT",
        "Delegated Regulation (EU) 2021/1253 Art. 2(7)(b)",
    ),
    (
        "21030_Preference_C_PAI",
        "10030_Preference_C_PAI",
        "equal",
        "Principal adverse impact flag differs between the EET and the EMT",
        "Delegated Regulation (EU) 2021/1253 Art. 2(7)(c)",
    ),
)


def cross_template_findings(
    eet_records: Iterable[Mapping[str, str]],
    emt_records: Iterable[Mapping[str, str]],
    *,
    eet_key: str = "00020_Portfolio_Identifying_Data",
    emt_key: str = "00010_Financial_Instrument_Identifier",
) -> FindingSet:
    """Reconcile the sustainability fields shared by the EET and the EMT.

    A distributor matching a client's sustainability preferences reads the EMT,
    while the substantiating SFDR data sits in the EET. If the two disagree the
    distributor is matching against a number the manufacturer cannot support.
    """

    findings = FindingSet()
    eet_by_key = {r.get(eet_key, ""): r for r in eet_records if r.get(eet_key)}
    emt_by_key = {r.get(emt_key, ""): r for r in emt_records if r.get(emt_key)}

    only_eet = sorted(set(eet_by_key) - set(emt_by_key))
    only_emt = sorted(set(emt_by_key) - set(eet_by_key))

    for identifier in only_eet:
        findings.add(
            Finding(
                rule_id="XTEMPLATE.COVERAGE.MISSING_EMT",
                reference="FinDatEx EET / EMT distribution pack",
                title="Share class present in the EET but not in the EMT",
                severity=Severity.WARNING,
                subject=identifier,
                message="A distributor receiving only this pack has no target market data for the share class.",
            )
        )
    for identifier in only_emt:
        findings.add(
            Finding(
                rule_id="XTEMPLATE.COVERAGE.MISSING_EET",
                reference="FinDatEx EET / EMT distribution pack",
                title="Share class present in the EMT but not in the EET",
                severity=Severity.WARNING,
                subject=identifier,
                message=(
                    "Without an EET row the distributor cannot evidence the SFDR data behind "
                    "the sustainability preferences quoted in the EMT."
                ),
            )
        )

    for identifier in sorted(set(eet_by_key) & set(emt_by_key)):
        eet = eet_by_key[identifier]
        emt = emt_by_key[identifier]
        for eet_field, emt_field, relation, title, reference in CROSS_TEMPLATE_CHECKS:
            left = (eet.get(eet_field) or "").strip()
            right = (emt.get(emt_field) or "").strip()
            if not left and not right:
                continue
            agree = _values_agree(left, right) if relation == "equal" else True
            findings.add(
                Finding(
                    rule_id=f"XTEMPLATE.{eet_field.split('_')[0]}",
                    reference=reference,
                    title=title,
                    severity=Severity.PASS if agree else Severity.BREACH,
                    subject=identifier,
                    message=(
                        "EET and EMT agree."
                        if agree
                        else f"EET reports {left or '(empty)'} but the EMT reports {right or '(empty)'}."
                    ),
                    detail={"eet_field": eet_field, "emt_field": emt_field},
                )
            )

    return findings


def _values_agree(left: str, right: str) -> bool:
    try:
        return abs(float(left.replace(",", ".")) - float(right.replace(",", "."))) < 1e-9
    except ValueError:
        return left.upper() == right.upper()
