"""Schema-driven template specifications and the condition language.

A template spec is JSON, not code, so a new EET or EMT version is a data change
rather than a release. Each spec declares its fields (code, type, domain of
values, whether it is mandatory, conditional or optional) and a list of
cross-field rules.

Condition language
------------------
Conditions are small JSON objects, composable with ``all``, ``any`` and ``not``::

    {"field": "SFDR_PRODUCT_TYPE", "in": ["ARTICLE_8", "ARTICLE_9"]}
    {"all": [{"field": "A", "present": true}, {"field": "B", "gt": 0}]}
    {"not": {"field": "C", "equals": "N"}}

Leaf operators: ``present``, ``equals``, ``not_equals``, ``in``, ``not_in``,
``gt``, ``gte``, ``lt``, ``lte``, ``matches``, and ``gte_field`` / ``lte_field``
for comparing two fields of the same record.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

#: Bundled specs ship inside the package so they survive installation.
SPEC_DIR = Path(__file__).resolve().parent / "specs"


class Requirement(str, Enum):
    """How strongly a field is required."""

    MANDATORY = "mandatory"
    #: Required only when a condition on another field holds.
    CONDITIONAL = "conditional"
    OPTIONAL = "optional"


class DataType(str, Enum):
    STRING = "string"
    DECIMAL = "decimal"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    ENUM = "enum"
    ISIN = "isin"
    LEI = "lei"
    CURRENCY = "currency"
    COUNTRY = "country"


@dataclass
class FieldSpec:
    """One column of a template."""

    code: str
    name: str
    datatype: DataType = DataType.STRING
    requirement: Requirement = Requirement.OPTIONAL
    values: list[str] = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    pattern: str | None = None
    #: Condition under which a ``CONDITIONAL`` field becomes mandatory.
    required_when: dict[str, Any] | None = None
    section: str = ""
    description: str = ""
    reference: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FieldSpec:
        return cls(
            code=payload["code"],
            name=payload["name"],
            datatype=DataType(payload.get("datatype", "string")),
            requirement=Requirement(payload.get("requirement", "optional")),
            values=list(payload.get("values", [])),
            minimum=payload.get("minimum"),
            maximum=payload.get("maximum"),
            pattern=payload.get("pattern"),
            required_when=payload.get("required_when"),
            section=payload.get("section", ""),
            description=payload.get("description", ""),
            reference=payload.get("reference", ""),
        )


@dataclass
class CrossFieldRule:
    """A consistency rule spanning more than one field."""

    rule_id: str
    title: str
    #: ``when`` holds, ``then`` must also hold.
    when: dict[str, Any]
    then: dict[str, Any]
    severity: str = "breach"
    message: str = ""
    reference: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CrossFieldRule:
        return cls(
            rule_id=payload["rule_id"],
            title=payload["title"],
            when=payload.get("when", {}),
            then=payload["then"],
            severity=payload.get("severity", "breach"),
            message=payload.get("message", ""),
            reference=payload.get("reference", ""),
        )


@dataclass
class TemplateSpec:
    """A complete template definition."""

    template: str
    version: str
    title: str
    description: str
    fields: list[FieldSpec]
    rules: list[CrossFieldRule]
    #: Field code that identifies a row in error messages.
    key_field: str = ""

    @property
    def by_code(self) -> dict[str, FieldSpec]:
        return {f.code: f for f in self.fields}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TemplateSpec:
        return cls(
            template=payload["template"],
            version=payload["version"],
            title=payload.get("title", payload["template"]),
            description=payload.get("description", ""),
            key_field=payload.get("key_field", ""),
            fields=[FieldSpec.from_dict(f) for f in payload["fields"]],
            rules=[CrossFieldRule.from_dict(r) for r in payload.get("rules", [])],
        )

    @classmethod
    def load(cls, path: str | Path) -> TemplateSpec:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def named(cls, name: str, spec_dir: str | Path | None = None) -> TemplateSpec:
        """Load a bundled spec by file stem, e.g. ``eet_v1_1_3``."""

        directory = Path(spec_dir) if spec_dir else SPEC_DIR
        path = directory / f"{name}.json"
        if not path.exists():
            available = ", ".join(sorted(p.stem for p in directory.glob("*.json"))) or "none"
            raise FileNotFoundError(
                f"no spec named {name!r} in {directory} (available: {available})"
            )
        return cls.load(path)


# --------------------------------------------------------------------------- #
# Condition evaluation
# --------------------------------------------------------------------------- #


class ConditionError(ValueError):
    """Raised when a condition in a spec is malformed."""


def _is_present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _as_number(value: Any) -> float | None:
    if not _is_present(value):
        return None
    try:
        return float(str(value).strip().replace(",", "."))
    except ValueError:
        return None


def evaluate(condition: Mapping[str, Any] | None, record: Mapping[str, Any]) -> bool:
    """Evaluate a condition against a record. An empty condition is always true."""

    if not condition:
        return True

    if "all" in condition:
        return all(evaluate(c, record) for c in condition["all"])
    if "any" in condition:
        return any(evaluate(c, record) for c in condition["any"])
    if "not" in condition:
        return not evaluate(condition["not"], record)

    if "field" not in condition:
        raise ConditionError(f"condition has no field and no connective: {condition!r}")

    value = record.get(condition["field"])

    if "present" in condition:
        return _is_present(value) is bool(condition["present"])
    if "equals" in condition:
        return _is_present(value) and str(value).strip() == str(condition["equals"])
    if "not_equals" in condition:
        return not (_is_present(value) and str(value).strip() == str(condition["not_equals"]))
    if "in" in condition:
        return _is_present(value) and str(value).strip() in {str(v) for v in condition["in"]}
    if "not_in" in condition:
        return not (
            _is_present(value) and str(value).strip() in {str(v) for v in condition["not_in"]}
        )
    if "matches" in condition:
        return (
            _is_present(value)
            and re.fullmatch(condition["matches"], str(value).strip()) is not None
        )

    number = _as_number(value)
    for operator, test in (
        ("gt", lambda a, b: a > b),
        ("gte", lambda a, b: a >= b),
        ("lt", lambda a, b: a < b),
        ("lte", lambda a, b: a <= b),
    ):
        if operator in condition:
            return number is not None and test(number, float(condition[operator]))

    for operator, test in (
        ("gte_field", lambda a, b: a >= b),
        ("lte_field", lambda a, b: a <= b),
    ):
        if operator in condition:
            other = _as_number(record.get(condition[operator]))
            return number is not None and other is not None and test(number, other)

    raise ConditionError(f"unrecognised condition operator in {condition!r}")


def describe(condition: Mapping[str, Any] | None) -> str:
    """Render a condition back into readable English for error messages."""

    if not condition:
        return "always"
    if "all" in condition:
        return " and ".join(describe(c) for c in condition["all"])
    if "any" in condition:
        return " or ".join(describe(c) for c in condition["any"])
    if "not" in condition:
        return f"not ({describe(condition['not'])})"

    name = condition.get("field", "?")
    if "present" in condition:
        return f"{name} is {'populated' if condition['present'] else 'empty'}"
    for operator, phrase in (
        ("equals", "is"),
        ("not_equals", "is not"),
        ("gt", "is greater than"),
        ("gte", "is at least"),
        ("lt", "is less than"),
        ("lte", "is at most"),
        ("matches", "matches"),
        ("gte_field", "is at least the value of"),
        ("lte_field", "is at most the value of"),
    ):
        if operator in condition:
            return f"{name} {phrase} {condition[operator]}"
    if "in" in condition:
        return f"{name} is one of {', '.join(str(v) for v in condition['in'])}"
    if "not_in" in condition:
        return f"{name} is none of {', '.join(str(v) for v in condition['not_in'])}"
    return str(condition)


# --------------------------------------------------------------------------- #
# Value-level type checking
# --------------------------------------------------------------------------- #

_LEI_RE = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_CCY_RE = re.compile(r"^[A-Z]{3}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_TRUE = {"true", "y", "yes", "1"}
_FALSE = {"false", "n", "no", "0"}


def isin_checksum_ok(isin: str) -> bool:
    """Validate an ISIN against its Luhn check digit (ISO 6166)."""

    if not _ISIN_RE.match(isin):
        return False
    digits = "".join(str(int(c, 36)) if c.isalpha() else c for c in isin)
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def check_value(spec: FieldSpec, raw: Any) -> str | None:
    """Return an error message if ``raw`` does not satisfy ``spec``, else ``None``."""

    if not _is_present(raw):
        return None
    value = str(raw).strip()

    if spec.datatype is DataType.ENUM or spec.values:
        if value not in spec.values:
            allowed = ", ".join(spec.values)
            return f"value {value!r} is not in the permitted domain ({allowed})"
        return None

    if spec.datatype in (DataType.DECIMAL, DataType.INTEGER):
        number = _as_number(value)
        if number is None:
            return f"value {value!r} is not numeric"
        if spec.datatype is DataType.INTEGER and number != int(number):
            return f"value {value!r} is not a whole number"
        if spec.minimum is not None and number < spec.minimum:
            return f"value {number:g} is below the minimum of {spec.minimum:g}"
        if spec.maximum is not None and number > spec.maximum:
            return f"value {number:g} is above the maximum of {spec.maximum:g}"
        return None

    if spec.datatype is DataType.BOOLEAN:
        if value.lower() not in _TRUE | _FALSE:
            return f"value {value!r} is not a boolean"
        return None

    if spec.datatype is DataType.DATE:
        try:
            date.fromisoformat(value)
        except ValueError:
            return f"value {value!r} is not an ISO 8601 date (YYYY-MM-DD)"
        return None

    if spec.datatype is DataType.ISIN:
        if not _ISIN_RE.match(value):
            return f"value {value!r} is not a well-formed ISIN"
        if not isin_checksum_ok(value):
            return f"ISIN {value} fails its check digit"
        return None

    if spec.datatype is DataType.LEI:
        if not _LEI_RE.match(value):
            return f"value {value!r} is not a well-formed 20-character LEI"
        return None

    if spec.datatype is DataType.CURRENCY and not _CCY_RE.match(value):
        return f"value {value!r} is not a three-letter ISO 4217 currency code"

    if spec.datatype is DataType.COUNTRY and not _COUNTRY_RE.match(value):
        return f"value {value!r} is not a two-letter ISO 3166 country code"

    if spec.pattern and not re.fullmatch(spec.pattern, value):
        return f"value {value!r} does not match the required pattern {spec.pattern}"

    return None
