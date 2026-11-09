"""A single finding vocabulary shared by every engine in the toolkit.

A UCITS breach, a PRIIPs data-quality problem and an EET field error are all the
same shape as far as a reviewer is concerned: a rule, a severity, a subject, and
the numbers that justify the verdict.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """Outcome of a single rule evaluation."""

    PASS = "pass"
    #: Inside the hard limit but past an internal early-warning threshold.
    WARNING = "warning"
    #: Hard regulatory limit exceeded.
    BREACH = "breach"
    #: Rule could not be evaluated because required data was absent.
    INCONCLUSIVE = "inconclusive"


SEVERITY_ORDER = {
    Severity.PASS: 0,
    Severity.INCONCLUSIVE: 1,
    Severity.WARNING: 2,
    Severity.BREACH: 3,
}


@dataclass
class Finding:
    """One rule evaluated against one subject."""

    #: Stable machine-readable rule id, e.g. ``UCITS.ART52.1.5_10_40``.
    rule_id: str
    #: Legal reference, e.g. ``Directive 2009/65/EC Art. 52(1)``.
    reference: str
    #: Human-readable rule name.
    title: str
    severity: Severity
    #: What the rule was applied to: an issuer, a field code, a fund.
    subject: str = ""
    #: Observed value, normally a percentage of NAV.
    actual: float | None = None
    #: Regulatory threshold the actual value is tested against.
    limit: float | None = None
    unit: str = "% NAV"
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def headroom(self) -> float | None:
        """Distance to the limit; negative where the limit is exceeded."""
        if self.actual is None or self.limit is None:
            return None
        return self.limit - self.actual

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["severity"] = self.severity.value
        payload["headroom"] = self.headroom
        return payload


class FindingSet:
    """An ordered collection of findings with convenience roll-ups."""

    def __init__(self, findings: Iterable[Finding] | None = None) -> None:
        self._findings: list[Finding] = list(findings or [])

    def __iter__(self) -> Iterator[Finding]:
        return iter(self._findings)

    def __len__(self) -> int:
        return len(self._findings)

    def __getitem__(self, index: int) -> Finding:
        return self._findings[index]

    def add(self, finding: Finding) -> Finding:
        self._findings.append(finding)
        return finding

    def extend(self, findings: Iterable[Finding]) -> None:
        self._findings.extend(findings)

    def of(self, *severities: Severity) -> list[Finding]:
        wanted = set(severities)
        return [f for f in self._findings if f.severity in wanted]

    @property
    def breaches(self) -> list[Finding]:
        return self.of(Severity.BREACH)

    @property
    def warnings(self) -> list[Finding]:
        return self.of(Severity.WARNING)

    @property
    def worst(self) -> Severity:
        if not self._findings:
            return Severity.PASS
        return max((f.severity for f in self._findings), key=lambda s: SEVERITY_ORDER[s])

    @property
    def compliant(self) -> bool:
        return not self.breaches

    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for f in self._findings:
            out[f.severity.value] += 1
        return out

    def sorted_by_severity(self) -> list[Finding]:
        return sorted(
            self._findings,
            key=lambda f: (-SEVERITY_ORDER[f.severity], -(f.actual or 0.0), f.rule_id),
        )

    def to_list(self) -> list[dict[str, Any]]:
        return [f.to_dict() for f in self._findings]

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_list(), indent=indent)

    def summary_line(self) -> str:
        c = self.counts()
        return (
            f"{len(self)} rules evaluated: {c['pass']} pass, {c['warning']} warning, "
            f"{c['breach']} breach, {c['inconclusive']} inconclusive"
        )
