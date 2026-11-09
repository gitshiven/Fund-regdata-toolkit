"""Tests for the EET / EMT spec loader, condition language and validator."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from fundreg.findings import Severity
from fundreg.templates import (
    DataType,
    FieldSpec,
    Requirement,
    TemplateSpec,
    cross_template_findings,
    evaluate,
    isin_checksum_ok,
    load_eet_spec,
    load_emt_spec,
    read_delimited,
    render_findings_html,
    validate_file,
    validate_records,
)
from fundreg.templates.spec import ConditionError, check_value, describe

DATA = Path(__file__).resolve().parents[1] / "data"


class TestConditionLanguage(unittest.TestCase):
    record = {"a": "ARTICLE_8", "b": "10", "c": "", "d": "25"}

    def test_empty_condition_is_true(self) -> None:
        self.assertTrue(evaluate(None, self.record))
        self.assertTrue(evaluate({}, self.record))

    def test_leaf_operators(self) -> None:
        cases = [
            ({"field": "a", "equals": "ARTICLE_8"}, True),
            ({"field": "a", "not_equals": "ARTICLE_8"}, False),
            ({"field": "a", "in": ["ARTICLE_8", "ARTICLE_9"]}, True),
            ({"field": "a", "not_in": ["ARTICLE_8"]}, False),
            ({"field": "c", "present": False}, True),
            ({"field": "b", "present": True}, True),
            ({"field": "b", "gt": 5}, True),
            ({"field": "b", "gte": 10}, True),
            ({"field": "b", "lt": 10}, False),
            ({"field": "b", "lte": 10}, True),
            ({"field": "a", "matches": r"ARTICLE_\d"}, True),
            ({"field": "b", "lte_field": "d"}, True),
            ({"field": "d", "gte_field": "b"}, True),
            ({"field": "d", "lte_field": "b"}, False),
        ]
        for condition, expected in cases:
            self.assertEqual(evaluate(condition, self.record), expected, msg=str(condition))

    def test_connectives(self) -> None:
        self.assertTrue(
            evaluate(
                {"all": [{"field": "a", "equals": "ARTICLE_8"}, {"field": "b", "gt": 0}]},
                self.record,
            )
        )
        self.assertTrue(
            evaluate(
                {"any": [{"field": "a", "equals": "NOPE"}, {"field": "b", "gt": 0}]},
                self.record,
            )
        )
        self.assertFalse(evaluate({"not": {"field": "b", "gt": 0}}, self.record))

    def test_numeric_comparison_on_an_empty_field_is_false(self) -> None:
        self.assertFalse(evaluate({"field": "c", "gt": 0}, self.record))
        self.assertFalse(evaluate({"field": "missing", "gte": 1}, self.record))

    def test_comma_decimal_separators_are_accepted(self) -> None:
        self.assertTrue(evaluate({"field": "x", "gt": 1}, {"x": "1,5"}))

    def test_malformed_conditions_raise(self) -> None:
        with self.assertRaises(ConditionError):
            evaluate({"gt": 1}, self.record)
        with self.assertRaises(ConditionError):
            evaluate({"field": "a", "wat": 1}, self.record)

    def test_describe_produces_readable_english(self) -> None:
        text = describe({"all": [{"field": "a", "equals": "X"}, {"field": "b", "gte": 3}]})
        self.assertEqual(text, "a is X and b is at least 3")


class TestValueChecking(unittest.TestCase):
    def test_isin_checksum(self) -> None:
        self.assertTrue(isin_checksum_ok("LU1234567AB9"))
        self.assertTrue(isin_checksum_ok("US0378331005"))
        self.assertFalse(isin_checksum_ok("LU1234567AB0"))
        self.assertFalse(isin_checksum_ok("NOTANISIN"))

    def test_enum_domain(self) -> None:
        spec = FieldSpec(code="x", name="x", datatype=DataType.ENUM, values=["Y", "N"])
        self.assertIsNone(check_value(spec, "Y"))
        self.assertIn("permitted domain", check_value(spec, "MAYBE"))

    def test_numeric_bounds(self) -> None:
        spec = FieldSpec(code="x", name="x", datatype=DataType.DECIMAL, minimum=0, maximum=100)
        self.assertIsNone(check_value(spec, "55.5"))
        self.assertIn("below the minimum", check_value(spec, "-1"))
        self.assertIn("above the maximum", check_value(spec, "101"))
        self.assertIn("not numeric", check_value(spec, "abc"))

    def test_integers_reject_fractions(self) -> None:
        spec = FieldSpec(code="x", name="x", datatype=DataType.INTEGER)
        self.assertIsNone(check_value(spec, "4"))
        self.assertIn("whole number", check_value(spec, "4.5"))

    def test_dates_currencies_countries_and_leis(self) -> None:
        checks = [
            (DataType.DATE, "2026-09-30", None),
            (DataType.DATE, "30/09/2026", "ISO 8601"),
            (DataType.CURRENCY, "EUR", None),
            (DataType.CURRENCY, "Euro", "ISO 4217"),
            (DataType.COUNTRY, "LU", None),
            (DataType.COUNTRY, "LUX", "ISO 3166"),
            (DataType.LEI, "5493001KJTIIGC8Y1R12", None),
            (DataType.LEI, "TOOSHORT", "20-character LEI"),
        ]
        for datatype, value, expected in checks:
            spec = FieldSpec(code="x", name="x", datatype=datatype)
            error = check_value(spec, value)
            if expected is None:
                self.assertIsNone(error, msg=f"{datatype}/{value}")
            else:
                self.assertIn(expected, error or "", msg=f"{datatype}/{value}")

    def test_booleans(self) -> None:
        spec = FieldSpec(code="x", name="x", datatype=DataType.BOOLEAN)
        for value in ("true", "FALSE", "Y", "n", "1", "0"):
            self.assertIsNone(check_value(spec, value), msg=value)
        self.assertIn("not a boolean", check_value(spec, "maybe"))

    def test_empty_values_are_not_type_checked(self) -> None:
        spec = FieldSpec(code="x", name="x", datatype=DataType.DECIMAL)
        self.assertIsNone(check_value(spec, ""))


class TestSpecLoading(unittest.TestCase):
    def test_bundled_specs_load(self) -> None:
        for spec in (load_eet_spec(), load_emt_spec()):
            self.assertTrue(spec.fields)
            self.assertTrue(spec.rules)
            self.assertTrue(spec.key_field)
            self.assertIn(spec.key_field, spec.by_code)

    def test_every_rule_references_declared_fields(self) -> None:
        from fundreg.templates.validator import _fields_in

        for spec in (load_eet_spec(), load_emt_spec()):
            known = set(spec.by_code)
            for rule in spec.rules:
                used = _fields_in(rule.when) | _fields_in(rule.then)
                self.assertTrue(
                    used <= known,
                    msg=f"{rule.rule_id} references unknown fields {used - known}",
                )

    def test_every_conditional_field_has_a_condition(self) -> None:
        for spec in (load_eet_spec(), load_emt_spec()):
            for field in spec.fields:
                if field.requirement is Requirement.CONDITIONAL:
                    self.assertIsNotNone(field.required_when, msg=field.code)

    def test_unknown_spec_name_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            TemplateSpec.named("no_such_template")


class TestFieldValidation(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = TemplateSpec.from_dict(
            {
                "template": "TEST",
                "version": "1",
                "key_field": "id",
                "fields": [
                    {"code": "id", "name": "Identifier", "requirement": "mandatory"},
                    {
                        "code": "type",
                        "name": "Type",
                        "datatype": "enum",
                        "requirement": "mandatory",
                        "values": ["A", "B"],
                    },
                    {
                        "code": "share",
                        "name": "Share",
                        "datatype": "decimal",
                        "requirement": "conditional",
                        "minimum": 0,
                        "maximum": 100,
                        "required_when": {"field": "type", "equals": "B"},
                    },
                ],
                "rules": [
                    {
                        "rule_id": "TEST.RULE",
                        "title": "B implies a positive share",
                        "when": {"field": "type", "equals": "B"},
                        "then": {"field": "share", "gt": 0},
                        "message": "B needs a share.",
                    }
                ],
            }
        )

    def test_missing_mandatory_field(self) -> None:
        result = validate_records([{"id": "", "type": "A"}], self.spec)
        codes = {(f.rule_id, f.subject) for f in result.findings.breaches}
        self.assertIn(("TEST.FIELD.MANDATORY", "row 1 / id"), codes)

    def test_conditional_field_only_required_when_the_condition_holds(self) -> None:
        ok = validate_records([{"id": "X", "type": "A", "share": ""}], self.spec)
        self.assertTrue(ok.valid)
        bad = validate_records([{"id": "X", "type": "B", "share": ""}], self.spec)
        self.assertFalse(bad.valid)
        self.assertTrue(any(f.rule_id == "TEST.FIELD.CONDITIONAL" for f in bad.findings.breaches))

    def test_value_domain_is_enforced(self) -> None:
        result = validate_records([{"id": "X", "type": "C"}], self.spec)
        self.assertTrue(any(f.rule_id == "TEST.FIELD.VALUE" for f in result.findings.breaches))

    def test_cross_field_rule_passes_and_fails(self) -> None:
        good = validate_records([{"id": "X", "type": "B", "share": "10"}], self.spec)
        self.assertTrue(good.valid)
        self.assertTrue(any(f.rule_id == "TEST.RULE" for f in good.findings.of(Severity.PASS)))

        bad = validate_records([{"id": "X", "type": "B", "share": "0"}], self.spec)
        rule = next(f for f in bad.findings.breaches if f.rule_id == "TEST.RULE")
        self.assertEqual(rule.message, "B needs a share.")
        self.assertEqual(rule.detail["values"], {"type": "B", "share": "0"})

    def test_rule_does_not_fire_when_its_precondition_fails(self) -> None:
        result = validate_records([{"id": "X", "type": "A"}], self.spec)
        self.assertFalse(any(f.rule_id == "TEST.RULE" for f in result.findings))

    def test_duplicate_keys_are_reported(self) -> None:
        result = validate_records([{"id": "X", "type": "A"}, {"id": "X", "type": "A"}], self.spec)
        self.assertTrue(
            any(f.rule_id == "TEST.STRUCTURE.DUPLICATE_KEY" for f in result.findings.breaches)
        )

    def test_unknown_columns_warn_rather_than_fail(self) -> None:
        result = validate_records([{"id": "X", "type": "A", "surprise": "1"}], self.spec)
        self.assertTrue(result.valid)
        self.assertTrue(
            any(f.rule_id == "TEST.STRUCTURE.UNKNOWN_COLUMN" for f in result.findings.warnings)
        )

    def test_missing_mandatory_column_is_reported_once(self) -> None:
        result = validate_records([{"id": "X"}, {"id": "Y"}], self.spec)
        structural = [f for f in result.findings if f.rule_id == "TEST.STRUCTURE.MISSING_COLUMN"]
        self.assertEqual(len(structural), 1)
        self.assertIn("type", structural[0].detail["columns"])


class TestSampleTemplates(unittest.TestCase):
    """Regression over the committed samples, which carry deliberate errors."""

    def test_eet_finds_the_planted_problems(self) -> None:
        result = validate_file(DATA / "sample_eet.csv", load_eet_spec())
        self.assertEqual(result.row_count, 3)
        self.assertFalse(result.valid)
        breached = {f.rule_id for f in result.findings.breaches}
        self.assertIn("EET.TAXONOMY.SUBSET_OF_SUSTAINABLE", breached)
        self.assertIn("EET.SFDR.ART6_NO_SUSTAINABLE_CLAIM", breached)
        self.assertIn("EET.PAI.PREFERENCE_C_CONSISTENCY", breached)
        warned = {f.rule_id for f in result.findings.warnings}
        self.assertIn("EET.PAI.MANDATORY_INDICATOR_COUNT", warned)

    def test_emt_finds_the_planted_problems(self) -> None:
        result = validate_file(DATA / "sample_emt.csv", load_emt_spec())
        self.assertEqual(result.row_count, 3)
        breached = {f.rule_id for f in result.findings.breaches}
        self.assertIn("EMT.TM.DISTRIBUTION_STRATEGY_REQUIRED", breached)
        self.assertIn("EMT.TM.LOSS_BEARING_CONTRADICTION", breached)
        warned = {f.rule_id for f in result.findings.warnings}
        self.assertIn("EMT.TM.HIGH_RISK_NOT_FOR_CAPITAL_PRESERVATION", warned)

    def test_the_clean_row_raises_nothing(self) -> None:
        for path, spec, key in (
            (DATA / "sample_eet.csv", load_eet_spec(), "LU1234567AB9"),
            (DATA / "sample_emt.csv", load_emt_spec(), "LU1234567AB9"),
        ):
            result = validate_file(path, spec)
            problems = [
                f for f in result.findings.breaches + result.findings.warnings if key in f.subject
            ]
            self.assertEqual(problems, [], msg=f"{spec.template}: {problems}")

    def test_summary_is_json_serialisable(self) -> None:
        result = validate_file(DATA / "sample_eet.csv", load_eet_spec())
        json.dumps(result.summary())
        json.loads(result.findings.to_json())


class TestCrossTemplate(unittest.TestCase):
    def test_sample_pack_disagrees_on_two_fields(self) -> None:
        findings = cross_template_findings(
            read_delimited(DATA / "sample_eet.csv"),
            read_delimited(DATA / "sample_emt.csv"),
        )
        breaches = findings.breaches
        self.assertEqual(len(breaches), 2)
        self.assertTrue(all("LU" in f.subject for f in breaches))

    def test_missing_counterpart_rows_are_flagged(self) -> None:
        eet = [{"00020_Portfolio_Identifying_Data": "LU0000000001"}]
        emt = [{"00010_Financial_Instrument_Identifier": "LU0000000002"}]
        rule_ids = {f.rule_id for f in cross_template_findings(eet, emt)}
        self.assertEqual(
            rule_ids, {"XTEMPLATE.COVERAGE.MISSING_EMT", "XTEMPLATE.COVERAGE.MISSING_EET"}
        )

    def test_matching_values_pass(self) -> None:
        eet = [
            {
                "00020_Portfolio_Identifying_Data": "LU1",
                "21010_Preference_A_Taxonomy_Share": "8.0",
                "21020_Preference_B_SFDR_Share": "25",
                "21030_Preference_C_PAI": "Y",
            }
        ]
        emt = [
            {
                "00010_Financial_Instrument_Identifier": "LU1",
                "10010_Preference_A_Taxonomy_Share": "8.00",
                "10020_Preference_B_SFDR_Share": "25.0",
                "10030_Preference_C_PAI": "y",
            }
        ]
        self.assertTrue(cross_template_findings(eet, emt).compliant)


class TestDelimiterSniffing(unittest.TestCase):
    def test_semicolon_files_are_read(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "semi.csv"
            path.write_text("a;b\n1;2\n", encoding="utf-8")
            self.assertEqual(read_delimited(path), [{"a": "1", "b": "2"}])

    def test_byte_order_marks_are_stripped(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bom.csv"
            path.write_bytes("a,b\n1,2\n".encode("utf-8-sig"))
            self.assertEqual(read_delimited(path), [{"a": "1", "b": "2"}])


class TestReportRendering(unittest.TestCase):
    def test_report_is_self_contained(self) -> None:
        result = validate_file(DATA / "sample_eet.csv", load_eet_spec())
        html = render_findings_html(result.findings, title="EET validation", subtitle="sample")
        self.assertIn("<!doctype html>", html)
        self.assertIn("EET validation", html)
        self.assertNotIn("<script src=", html)
        self.assertNotIn('<link rel="stylesheet"', html)

    def test_messages_are_html_escaped(self) -> None:
        from fundreg.findings import Finding, FindingSet

        findings = FindingSet(
            [
                Finding(
                    rule_id="X",
                    reference="",
                    title="<script>alert(1)</script>",
                    severity=Severity.BREACH,
                    subject="row",
                )
            ]
        )
        html = render_findings_html(findings, title="t")
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
