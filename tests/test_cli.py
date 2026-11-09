"""End-to-end tests for the command line interface and its exit codes."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from fundreg.cli import EXIT_BREACH, EXIT_ERROR, EXIT_OK, main

DATA = Path(__file__).resolve().parents[1] / "data"
PORTFOLIO = str(DATA / "sample_portfolio.csv")
RETURNS = str(DATA / "returns" / "meridian_european_equity.csv")
EET = str(DATA / "sample_eet.csv")
EMT = str(DATA / "sample_emt.csv")


def run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue() + err.getvalue()


class TestExitCodes(unittest.TestCase):
    def test_a_breaching_portfolio_exits_one(self) -> None:
        code, output = run(["ucits", PORTFOLIO])
        self.assertEqual(code, EXIT_BREACH)
        self.assertIn("UCITS.ART52.1A.SINGLE_ISSUER", output)
        self.assertIn("Global exposure", output)

    def test_a_clean_portfolio_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clean.csv"
            path.write_text(
                "#fund_name=Clean Fund\n#nav=1000000\n"
                "instrument_id,name,asset_class,issuer_id,market_value,quantity\n"
                "EQ_A,A,equity,A,40000,1\n"
                "EQ_B,B,equity,B,40000,1\n",
                encoding="utf-8",
            )
            code, _ = run(["ucits", str(path)])
            self.assertEqual(code, EXIT_OK)

    def test_missing_file_exits_two(self) -> None:
        code, output = run(["ucits", "does_not_exist.csv"])
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("error:", output)

    def test_eet_and_emt_detect_their_planted_errors(self) -> None:
        for command, path in (("eet", EET), ("emt", EMT)):
            code, output = run([command, path])
            self.assertEqual(code, EXIT_BREACH, msg=command)
            self.assertIn("FAIL", output)

    def test_reconcile_detects_the_mismatch(self) -> None:
        code, output = run(["reconcile", EET, EMT])
        self.assertEqual(code, EXIT_BREACH)
        self.assertIn("XTEMPLATE", output)

    def test_priips_always_succeeds(self) -> None:
        code, output = run(["priips", RETURNS, "--rhp", "5"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("SRI 4 of 7", output)
        self.assertIn("Performance scenarios", output)


class TestOutputFiles(unittest.TestCase):
    def test_ucits_writes_html_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "nested" / "ucits.html"
            payload = Path(tmp) / "ucits.json"
            run(["ucits", PORTFOLIO, "--html", str(html), "--json", str(payload)])
            self.assertIn("<!doctype html>", html.read_text(encoding="utf-8"))
            rows = json.loads(payload.read_text(encoding="utf-8"))
            self.assertTrue(any(r["rule_id"] == "UCITS.ART52.1A.SINGLE_ISSUER" for r in rows))

    def test_priips_writes_a_kid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "kid.html"
            payload = Path(tmp) / "kid.json"
            run(
                [
                    "priips",
                    RETURNS,
                    "--rhp",
                    "5",
                    "--name",
                    "Test Fund",
                    "--html",
                    str(html),
                    "--json",
                    str(payload),
                ]
            )
            self.assertIn("Key Information Document", html.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(payload.read_text(encoding="utf-8"))["risk"]["sri"], 4)


class TestOptions(unittest.TestCase):
    def test_all_flag_shows_passing_rules(self) -> None:
        _, quiet = run(["ucits", PORTFOLIO])
        _, verbose = run(["ucits", PORTFOLIO, "--all"])
        self.assertGreater(verbose.count("[pass]"), 0)
        self.assertEqual(quiet.count("[pass]"), 0)

    def test_single_issuer_limit_is_configurable(self) -> None:
        _, strict = run(["ucits", PORTFOLIO, "--single-issuer-limit", "5"])
        self.assertIn("/   5.00", strict)

    def test_cornish_fisher_method_can_be_selected(self) -> None:
        _, output = run(["priips", RETURNS, "--rhp", "5", "--method", "cornish_fisher"])
        self.assertIn("Cornish-Fisher", output)

    def test_an_alternative_spec_can_be_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "mini.json"
            spec.write_text(
                json.dumps(
                    {
                        "template": "MINI",
                        "version": "1",
                        "key_field": "00010_Financial_Instrument_Identifier",
                        "fields": [
                            {
                                "code": "00010_Financial_Instrument_Identifier",
                                "name": "ISIN",
                                "datatype": "isin",
                                "requirement": "mandatory",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            code, output = run(["emt", EMT, "--spec", str(spec)])
            self.assertEqual(code, EXIT_OK)
            self.assertIn("MINI", output)

    def test_version_flag(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            run(["--version"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
