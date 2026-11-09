"""FinDatEx-style template validation: EET (SFDR) and EMT (MiFID II).

The specs are JSON, so supporting a new template version is a data change.
Bundled specs live in ``fundreg/templates/specs``.
"""

from fundreg.templates.report import render_findings_html, write_findings_html
from fundreg.templates.spec import (
    CrossFieldRule,
    DataType,
    FieldSpec,
    Requirement,
    TemplateSpec,
    evaluate,
    isin_checksum_ok,
)
from fundreg.templates.validator import (
    ValidationResult,
    cross_template_findings,
    read_delimited,
    validate_file,
    validate_records,
)

#: Convenience names for the bundled specs.
EET = "eet_v1_1_3"
EMT = "emt_v4_1"


def load_eet_spec() -> TemplateSpec:
    return TemplateSpec.named(EET)


def load_emt_spec() -> TemplateSpec:
    return TemplateSpec.named(EMT)


__all__ = [
    "CrossFieldRule",
    "DataType",
    "EET",
    "EMT",
    "FieldSpec",
    "Requirement",
    "TemplateSpec",
    "ValidationResult",
    "cross_template_findings",
    "evaluate",
    "isin_checksum_ok",
    "load_eet_spec",
    "load_emt_spec",
    "read_delimited",
    "render_findings_html",
    "validate_file",
    "validate_records",
    "write_findings_html",
]
