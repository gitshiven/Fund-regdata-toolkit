"""Serialise an Annex IV record as ESMA-style reporting XML.

The document follows the shape of the ESMA AIFMD reporting file: an
``AIFMReportingInfo`` envelope carrying the Member State and creation stamp,
with one ``AIFRecordInfo`` element per fund. Elements are emitted in the order
the spec declares them, so the output is stable and diffable.

Built on :mod:`xml.etree.ElementTree` from the standard library rather than a
schema binding, because the authoritative XSD is published by ESMA and changes
between versions; this writes the structure, not the contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from fundreg.aifmd.report import AnnexIVReport, load_annex_iv_spec

#: Elements that live on the envelope rather than inside a fund record.
ENVELOPE_FIELDS = ("ReportingMemberState",)

#: Version attribute written on the envelope.
SCHEMA_VERSION = "1.2"


def _element_order() -> list[str]:
    """Annex IV element names in the order the spec declares them."""

    return [f.code for f in load_annex_iv_spec().fields]


def build_tree(
    records: Iterable[Mapping[str, str]],
    *,
    reporting_member_state: str = "LU",
    created: str | None = None,
) -> ElementTree.Element:
    """Build the ``AIFMReportingInfo`` tree for one or more AIF records."""

    stamp = created or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    root = ElementTree.Element(
        "AIFMReportingInfo",
        {
            "CreationDateAndTime": stamp,
            "Version": SCHEMA_VERSION,
            "ReportingMemberState": reporting_member_state,
        },
    )

    order = _element_order()
    for record in records:
        node = ElementTree.SubElement(root, "AIFRecordInfo")
        for code in order:
            if code in ENVELOPE_FIELDS:
                continue
            value = (record.get(code) or "").strip()
            if not value:
                # An empty optional element is noise; omit it rather than
                # filing a blank the regulator has to interpret.
                continue
            ElementTree.SubElement(node, code).text = value
        # Anything the spec does not declare still belongs in the file, so it
        # is appended rather than silently dropped.
        for code in sorted(set(record) - set(order)):
            value = (record.get(code) or "").strip()
            if value:
                ElementTree.SubElement(node, code).text = value

    return root


def to_xml(
    records: Iterable[Mapping[str, str]],
    *,
    reporting_member_state: str = "LU",
    created: str | None = None,
) -> str:
    """Render records as an indented, declaration-prefixed XML document."""

    root = build_tree(records, reporting_member_state=reporting_member_state, created=created)
    ElementTree.indent(root, space="  ")
    body = ElementTree.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def report_to_xml(report: AnnexIVReport, *, created: str | None = None) -> str:
    """Render a single :class:`~fundreg.aifmd.report.AnnexIVReport`."""

    return to_xml(
        [report.record],
        reporting_member_state=report.aifm.reporting_member_state,
        created=created,
    )


def write_xml(report: AnnexIVReport, path: str | Path, *, created: str | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_to_xml(report, created=created), encoding="utf-8")
    return path
