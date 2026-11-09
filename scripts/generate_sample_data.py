"""Generate the sample data the examples and the dashboard run on.

Produces:

* ``data/returns/<fund>.csv``  - ten years of daily returns per share class,
  drawn from a seeded generator with fat tails and volatility clustering so the
  PRIIPs skewness and kurtosis terms actually do something.
* ``data/sample_eet.csv``      - a European ESG Template with deliberate errors.
* ``data/sample_emt.csv``      - a European MiFID Template with deliberate errors.

Everything is deterministic: the same seed gives the same files, so the tests
and the published dashboard do not drift.

Run from the repository root::

    python scripts/generate_sample_data.py
"""

from __future__ import annotations

import csv
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DAYS_PER_YEAR = 256
YEARS = 10


def isin_check_digit(body: str) -> str:
    """Compute the ISO 6166 check digit for an eleven-character ISIN body."""

    digits = "".join(str(int(c, 36)) if c.isalpha() else c for c in body)
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 0:  # the check digit will occupy position 0
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return str((10 - total % 10) % 10)


def isin(body: str) -> str:
    return body + isin_check_digit(body)


# Three share classes with distinct risk profiles, so the SRI lands on three
# different classes and the dashboard has something to compare.
FUNDS = [
    {
        "key": "meridian_european_equity",
        "name": "Meridian European Equity Fund A EUR Acc",
        "isin_body": "LU1234567AB",
        "seed": 20260101,
        "annual_drift": 0.072,
        "annual_vol": 0.172,
        "jump_probability": 0.004,
        "jump_scale": 0.045,
        "sfdr": "ARTICLE_8",
        "rhp_years": 5,
    },
    {
        "key": "meridian_climate_transition",
        "name": "Meridian Climate Transition Fund I EUR Acc",
        "isin_body": "LU7654321CD",
        "seed": 20260109,
        "annual_drift": 0.081,
        "annual_vol": 0.231,
        "jump_probability": 0.006,
        "jump_scale": 0.055,
        "sfdr": "ARTICLE_9",
        "rhp_years": 7,
    },
    {
        "key": "meridian_global_bond",
        "name": "Meridian Global Bond Fund A EUR Dis",
        "isin_body": "LU2468013EF",
        "seed": 20260110,
        "annual_drift": 0.029,
        "annual_vol": 0.052,
        "jump_probability": 0.003,
        "jump_scale": 0.018,
        "sfdr": "ARTICLE_6",
        "rhp_years": 3,
    },
]


def simulate_returns(fund: dict, seed: int) -> list[float]:
    """A GARCH(1,1) return series with compensated downward jumps.

    Plain Gaussian returns would make the Cornish-Fisher skewness and kurtosis
    terms vanish, which hides exactly the part of the PRIIPs methodology worth
    demonstrating. Two details keep the series honest:

    * only the diffusive shock feeds the variance recursion, so a jump does not
      detonate the conditional volatility for the following months;
    * the drift carries a jump compensator, so the expected log return over the
      whole sample is the stated drift rather than the drift minus the jumps.
    """

    rng = random.Random(seed)
    n = DAYS_PER_YEAR * YEARS
    target_var = (fund["annual_vol"] / math.sqrt(DAYS_PER_YEAR)) ** 2

    probability = fund["jump_probability"]
    scale = fund["jump_scale"]
    # E[-|N(0, scale)|] = -scale * sqrt(2/pi), so this is the expected daily
    # drag the jump component applies.
    jump_drag = -probability * scale * math.sqrt(2.0 / math.pi)
    mu = fund["annual_drift"] / DAYS_PER_YEAR - jump_drag

    omega, alpha, beta = target_var * 0.05, 0.08, 0.87
    variance = target_var
    previous_shock = 0.0
    out: list[float] = []

    for _ in range(n):
        variance = omega + alpha * previous_shock**2 + beta * variance
        shock = rng.gauss(0.0, math.sqrt(variance))
        previous_shock = shock
        jump = -abs(rng.gauss(0.0, scale)) if rng.random() < probability else 0.0
        out.append(math.exp(mu + shock + jump) - 1.0)

    return out


def write_returns(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for fund in FUNDS:
        rows = simulate_returns(fund, seed=fund["seed"])
        path = directory / f"{fund['key']}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["return"])
            writer.writerows([f"{r:.8f}"] for r in rows)
        print(f"wrote {len(rows):,} returns to {path.relative_to(ROOT)}")


EET_COLUMNS = [
    "00010_Portfolio_Identifying_Data_Type",
    "00020_Portfolio_Identifying_Data",
    "00030_Portfolio_Name",
    "00040_Financial_Instrument_Currency",
    "00050_Reporting_Date",
    "00060_Manufacturer_LEI",
    "20010_SFDR_Product_Type",
    "20020_Sustainable_Investment_Minimum_Share",
    "20030_Taxonomy_Aligned_Minimum_Share",
    "20040_Environmental_Characteristics",
    "20050_Social_Characteristics",
    "20060_PAI_Considered",
    "20070_PAI_Mandatory_Indicators_Covered",
    "21010_Preference_A_Taxonomy_Share",
    "21020_Preference_B_SFDR_Share",
    "21030_Preference_C_PAI",
    "22010_PAI_1_GHG_Scope_1",
    "22020_PAI_2_Carbon_Footprint",
    "22040_PAI_4_Fossil_Fuel_Share",
    "22100_PAI_10_UNGC_OECD_Violations_Share",
    "22130_PAI_13_Board_Gender_Diversity",
    "22900_PAI_Coverage_Ratio",
    "23010_Taxonomy_Aligned_Turnover",
    "23020_Taxonomy_Aligned_CapEx",
    "23030_Taxonomy_Aligned_OpEx",
]

LEI = "5493001KJTIIGC8Y1R12"
REPORTING_DATE = "2026-09-30"


def eet_rows() -> list[dict[str, str]]:
    """Three share classes, each carrying a different realistic data problem."""

    equity, climate, bond = (isin(f["isin_body"]) for f in FUNDS)

    return [
        # Clean Article 8 row - the control case.
        {
            "00010_Portfolio_Identifying_Data_Type": "ISIN",
            "00020_Portfolio_Identifying_Data": equity,
            "00030_Portfolio_Name": FUNDS[0]["name"],
            "00040_Financial_Instrument_Currency": "EUR",
            "00050_Reporting_Date": REPORTING_DATE,
            "00060_Manufacturer_LEI": LEI,
            "20010_SFDR_Product_Type": "ARTICLE_8",
            "20020_Sustainable_Investment_Minimum_Share": "25.0",
            "20030_Taxonomy_Aligned_Minimum_Share": "8.0",
            "20040_Environmental_Characteristics": "true",
            "20050_Social_Characteristics": "true",
            "20060_PAI_Considered": "Y",
            "20070_PAI_Mandatory_Indicators_Covered": "14",
            "21010_Preference_A_Taxonomy_Share": "8.0",
            "21020_Preference_B_SFDR_Share": "25.0",
            "21030_Preference_C_PAI": "Y",
            "22010_PAI_1_GHG_Scope_1": "14250.5",
            "22020_PAI_2_Carbon_Footprint": "61.4",
            "22040_PAI_4_Fossil_Fuel_Share": "2.1",
            "22100_PAI_10_UNGC_OECD_Violations_Share": "0.0",
            "22130_PAI_13_Board_Gender_Diversity": "37.8",
            "22900_PAI_Coverage_Ratio": "91.2",
            "23010_Taxonomy_Aligned_Turnover": "8.0",
            "23020_Taxonomy_Aligned_CapEx": "11.4",
            "23030_Taxonomy_Aligned_OpEx": "6.2",
        },
        # Article 9 row: taxonomy share exceeds the sustainable-investment share
        # it is a subset of, and only nine mandatory PAI indicators are covered.
        {
            "00010_Portfolio_Identifying_Data_Type": "ISIN",
            "00020_Portfolio_Identifying_Data": climate,
            "00030_Portfolio_Name": FUNDS[1]["name"],
            "00040_Financial_Instrument_Currency": "EUR",
            "00050_Reporting_Date": REPORTING_DATE,
            "00060_Manufacturer_LEI": LEI,
            "20010_SFDR_Product_Type": "ARTICLE_9",
            "20020_Sustainable_Investment_Minimum_Share": "60.0",
            "20030_Taxonomy_Aligned_Minimum_Share": "72.0",
            "20040_Environmental_Characteristics": "true",
            "20050_Social_Characteristics": "false",
            "20060_PAI_Considered": "Y",
            "20070_PAI_Mandatory_Indicators_Covered": "9",
            "21010_Preference_A_Taxonomy_Share": "72.0",
            "21020_Preference_B_SFDR_Share": "60.0",
            "21030_Preference_C_PAI": "Y",
            "22010_PAI_1_GHG_Scope_1": "3180.0",
            "22020_PAI_2_Carbon_Footprint": "18.7",
            "22040_PAI_4_Fossil_Fuel_Share": "0.0",
            "22100_PAI_10_UNGC_OECD_Violations_Share": "0.0",
            "22130_PAI_13_Board_Gender_Diversity": "41.5",
            "22900_PAI_Coverage_Ratio": "46.0",
            "23010_Taxonomy_Aligned_Turnover": "72.0",
            "23020_Taxonomy_Aligned_CapEx": "78.1",
            "23030_Taxonomy_Aligned_OpEx": "65.9",
        },
        # Article 6 row that nonetheless advertises a sustainable-investment
        # share and a preference B match, and claims PAI consideration it does
        # not have.
        {
            "00010_Portfolio_Identifying_Data_Type": "ISIN",
            "00020_Portfolio_Identifying_Data": bond,
            "00030_Portfolio_Name": FUNDS[2]["name"],
            "00040_Financial_Instrument_Currency": "EUR",
            "00050_Reporting_Date": REPORTING_DATE,
            "00060_Manufacturer_LEI": LEI,
            "20010_SFDR_Product_Type": "ARTICLE_6",
            "20020_Sustainable_Investment_Minimum_Share": "10.0",
            "20030_Taxonomy_Aligned_Minimum_Share": "",
            "20040_Environmental_Characteristics": "",
            "20050_Social_Characteristics": "",
            "20060_PAI_Considered": "N",
            "20070_PAI_Mandatory_Indicators_Covered": "",
            "21010_Preference_A_Taxonomy_Share": "0.0",
            "21020_Preference_B_SFDR_Share": "10.0",
            "21030_Preference_C_PAI": "Y",
            "22010_PAI_1_GHG_Scope_1": "",
            "22020_PAI_2_Carbon_Footprint": "",
            "22040_PAI_4_Fossil_Fuel_Share": "",
            "22100_PAI_10_UNGC_OECD_Violations_Share": "",
            "22130_PAI_13_Board_Gender_Diversity": "",
            "22900_PAI_Coverage_Ratio": "",
            "23010_Taxonomy_Aligned_Turnover": "",
            "23020_Taxonomy_Aligned_CapEx": "",
            "23030_Taxonomy_Aligned_OpEx": "",
        },
    ]


EMT_COLUMNS = [
    "00010_Financial_Instrument_Identifier",
    "00020_Financial_Instrument_Name",
    "00030_Reporting_Date",
    "00040_Manufacturer_LEI",
    "00050_Financial_Instrument_Currency",
    "02010_Investor_Type_Retail",
    "02020_Investor_Type_Professional",
    "02030_Investor_Type_Eligible_Counterparty",
    "03010_Knowledge_Basic",
    "03020_Knowledge_Informed",
    "03030_Knowledge_Advanced",
    "04010_Loss_Bearing_No_Capital_Loss",
    "04020_Loss_Bearing_Limited_Capital_Loss",
    "04030_Loss_Bearing_No_Capital_Guarantee",
    "04040_Loss_Bearing_Beyond_Capital",
    "05010_Risk_Tolerance_Indicator",
    "05020_Risk_Tolerance_Methodology",
    "06010_Objective_Preservation",
    "06020_Objective_Growth",
    "06030_Objective_Income",
    "06040_Objective_Hedging",
    "06050_Recommended_Holding_Period",
    "07010_Distribution_Execution_Only",
    "07020_Distribution_Appropriateness",
    "07030_Distribution_Investment_Advice",
    "07040_Distribution_Portfolio_Management",
    "08010_Negative_Target_Market",
    "09010_One_Off_Cost_Entry",
    "09020_One_Off_Cost_Exit",
    "09030_Ongoing_Cost",
    "09040_Transaction_Cost",
    "09050_Incidental_Cost_Performance_Fee",
    "09060_Total_Cost_Ratio",
    "10010_Preference_A_Taxonomy_Share",
    "10020_Preference_B_SFDR_Share",
    "10030_Preference_C_PAI",
]


def emt_rows() -> list[dict[str, str]]:
    equity, climate, bond = (isin(f["isin_body"]) for f in FUNDS)

    return [
        {
            "00010_Financial_Instrument_Identifier": equity,
            "00020_Financial_Instrument_Name": FUNDS[0]["name"],
            "00030_Reporting_Date": REPORTING_DATE,
            "00040_Manufacturer_LEI": LEI,
            "00050_Financial_Instrument_Currency": "EUR",
            "02010_Investor_Type_Retail": "Y",
            "02020_Investor_Type_Professional": "Y",
            "02030_Investor_Type_Eligible_Counterparty": "N",
            "03010_Knowledge_Basic": "Y",
            "03020_Knowledge_Informed": "Y",
            "03030_Knowledge_Advanced": "Y",
            "04010_Loss_Bearing_No_Capital_Loss": "N",
            "04020_Loss_Bearing_Limited_Capital_Loss": "N",
            "04030_Loss_Bearing_No_Capital_Guarantee": "Y",
            "04040_Loss_Bearing_Beyond_Capital": "N",
            "05010_Risk_Tolerance_Indicator": "4",
            "05020_Risk_Tolerance_Methodology": "PRIIPS_SRI",
            "06010_Objective_Preservation": "N",
            "06020_Objective_Growth": "Y",
            "06030_Objective_Income": "N",
            "06040_Objective_Hedging": "N",
            "06050_Recommended_Holding_Period": "5",
            "07010_Distribution_Execution_Only": "Y",
            "07020_Distribution_Appropriateness": "Y",
            "07030_Distribution_Investment_Advice": "Y",
            "07040_Distribution_Portfolio_Management": "Y",
            "08010_Negative_Target_Market": "Investors with a holding horizon below three years",
            "09010_One_Off_Cost_Entry": "0.00",
            "09020_One_Off_Cost_Exit": "0.00",
            "09030_Ongoing_Cost": "1.05",
            "09040_Transaction_Cost": "0.18",
            "09050_Incidental_Cost_Performance_Fee": "0.00",
            "09060_Total_Cost_Ratio": "1.23",
            "10010_Preference_A_Taxonomy_Share": "8.0",
            "10020_Preference_B_SFDR_Share": "25.0",
            "10030_Preference_C_PAI": "Y",
        },
        # Risk class 6 but still offered as capital preservation, and the
        # sustainability preference figures disagree with the EET.
        {
            "00010_Financial_Instrument_Identifier": climate,
            "00020_Financial_Instrument_Name": FUNDS[1]["name"],
            "00030_Reporting_Date": REPORTING_DATE,
            "00040_Manufacturer_LEI": LEI,
            "00050_Financial_Instrument_Currency": "EUR",
            "02010_Investor_Type_Retail": "Y",
            "02020_Investor_Type_Professional": "Y",
            "02030_Investor_Type_Eligible_Counterparty": "N",
            "03010_Knowledge_Basic": "N",
            "03020_Knowledge_Informed": "Y",
            "03030_Knowledge_Advanced": "Y",
            "04010_Loss_Bearing_No_Capital_Loss": "N",
            "04020_Loss_Bearing_Limited_Capital_Loss": "N",
            "04030_Loss_Bearing_No_Capital_Guarantee": "Y",
            "04040_Loss_Bearing_Beyond_Capital": "N",
            "05010_Risk_Tolerance_Indicator": "6",
            "05020_Risk_Tolerance_Methodology": "PRIIPS_SRI",
            "06010_Objective_Preservation": "Y",
            "06020_Objective_Growth": "Y",
            "06030_Objective_Income": "N",
            "06040_Objective_Hedging": "N",
            "06050_Recommended_Holding_Period": "7",
            "07010_Distribution_Execution_Only": "Y",
            "07020_Distribution_Appropriateness": "Y",
            "07030_Distribution_Investment_Advice": "Y",
            "07040_Distribution_Portfolio_Management": "Y",
            "08010_Negative_Target_Market": "",
            "09010_One_Off_Cost_Entry": "0.00",
            "09020_One_Off_Cost_Exit": "0.00",
            "09030_Ongoing_Cost": "1.40",
            "09040_Transaction_Cost": "0.26",
            "09050_Incidental_Cost_Performance_Fee": "0.15",
            "09060_Total_Cost_Ratio": "1.30",
            "10010_Preference_A_Taxonomy_Share": "55.0",
            "10020_Preference_B_SFDR_Share": "60.0",
            "10030_Preference_C_PAI": "Y",
        },
        # No distribution channel is enabled and the loss-bearing profile
        # contradicts itself.
        {
            "00010_Financial_Instrument_Identifier": bond,
            "00020_Financial_Instrument_Name": FUNDS[2]["name"],
            "00030_Reporting_Date": REPORTING_DATE,
            "00040_Manufacturer_LEI": LEI,
            "00050_Financial_Instrument_Currency": "EUR",
            "02010_Investor_Type_Retail": "Y",
            "02020_Investor_Type_Professional": "Y",
            "02030_Investor_Type_Eligible_Counterparty": "N",
            "03010_Knowledge_Basic": "Y",
            "03020_Knowledge_Informed": "Y",
            "03030_Knowledge_Advanced": "Y",
            "04010_Loss_Bearing_No_Capital_Loss": "Y",
            "04020_Loss_Bearing_Limited_Capital_Loss": "Y",
            "04030_Loss_Bearing_No_Capital_Guarantee": "N",
            "04040_Loss_Bearing_Beyond_Capital": "Y",
            "05010_Risk_Tolerance_Indicator": "2",
            "05020_Risk_Tolerance_Methodology": "UCITS_SRRI",
            "06010_Objective_Preservation": "Y",
            "06020_Objective_Growth": "N",
            "06030_Objective_Income": "Y",
            "06040_Objective_Hedging": "N",
            "06050_Recommended_Holding_Period": "3",
            "07010_Distribution_Execution_Only": "N",
            "07020_Distribution_Appropriateness": "N",
            "07030_Distribution_Investment_Advice": "N",
            "07040_Distribution_Portfolio_Management": "N",
            "08010_Negative_Target_Market": "",
            "09010_One_Off_Cost_Entry": "0.00",
            "09020_One_Off_Cost_Exit": "0.00",
            "09030_Ongoing_Cost": "0.62",
            "09040_Transaction_Cost": "0.09",
            "09050_Incidental_Cost_Performance_Fee": "0.00",
            "09060_Total_Cost_Ratio": "0.71",
            "10010_Preference_A_Taxonomy_Share": "0.0",
            "10020_Preference_B_SFDR_Share": "10.0",
            "10030_Preference_C_PAI": "N",
        },
    ]


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {path.relative_to(ROOT)}")


def main() -> None:
    write_returns(ROOT / "data" / "returns")
    write_csv(ROOT / "data" / "sample_eet.csv", EET_COLUMNS, eet_rows())
    write_csv(ROOT / "data" / "sample_emt.csv", EMT_COLUMNS, emt_rows())
    for fund in FUNDS:
        print(f"{fund['name']}: {isin(fund['isin_body'])}")


if __name__ == "__main__":
    main()
