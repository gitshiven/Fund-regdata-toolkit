# fund-regdata-toolkit

A working implementation of the calculations that sit behind European fund
regulation: **UCITS** investment limits, the **PRIIPs** Key Information
Document, and validation of the **EET** and **EMT** files that carry **SFDR**
and **MiFID II** data from a manufacturer to its distributors.

Pure Python, no dependencies, 137 tests. A live dashboard runs every engine over
a sample fund range.

**[Open the dashboard](https://gitshiven.github.io/Fund-regdata-toolkit/)**

---

## Why this exists

Most of this work is done in spreadsheets that nobody can test. The rules are
public and precise, so they can be code: a limit is a number with a legal
reference, a risk class is a formula in an Annex, and a template field is either
populated or it is not. This repository treats them that way.

Every rule produces a **finding** — pass, warning, breach, or "no data" — with
the article it comes from, the number observed, the limit, and the headroom
between them. Rules that pass are reported too, because the question a
depositary or an internal auditor actually asks is not "were there breaches" but
"which limits did you test, against what numbers".

## What it computes

### UCITS investment limits — Directive 2009/65/EC

| Rule | Limit |
|---|---|
| `UCITS.ART50.2A.INELIGIBLE` | 10% in assets outside the Art. 50(1) eligible list |
| `UCITS.ART52.1A.SINGLE_ISSUER` | 5% per issuer, raised to 10% where the Member State permits |
| `UCITS.ART52.2.AGGREGATE_40` | 40% aggregate of issuer positions above 5% |
| `UCITS.ART52.1B.DEPOSIT` | 20% of deposits with a single body |
| `UCITS.ART52.1.OTC_COUNTERPARTY` | 10% credit institution / 5% other counterparty |
| `UCITS.ART52.2.COMBINED_20` | 20% combined securities, deposits and OTC per body |
| `UCITS.ART52.3.PUBLIC_BODY` | 35% per sovereign or public issuer, 100% under the derogation |
| `UCITS.ART52.4.COVERED_BOND` | 25% per covered bond issuer, 80% in aggregate |
| `UCITS.ART55.1.SINGLE_CIS` | 20% in a single UCITS or other collective investment undertaking |
| `UCITS.ART55.1.NON_UCITS_AGGREGATE` | 30% in non-UCITS undertakings |
| `UCITS.ART56.INFLUENCE` | 10% of an issue, 25% of a single fund |
| `UCITS.ART51.3.GLOBAL_EXPOSURE` | 100% of NAV, commitment approach per CESR/10-788 |
| `UCITS.ART83.BORROWING` | 10% temporary borrowing |

Three things the engine gets right that a naive implementation usually does not:

- **Look-through.** A single-name derivative adds to the issuer exposure of its
  underlying. An index, basket, rate or currency derivative does not, because
  there is no single obligor to attribute it to.
- **Art. 52(5) exclusions.** Public-body and covered bond holdings are excluded
  from the 40% aggregate, as are deposits and OTC derivatives.
- **Netting that is actually netting.** The commitment approach offsets long
  against short only where the contracts share both a netting set and an
  underlying, and reports the gross figure beside the net one so the offset can
  be challenged.

### PRIIPs Key Information Document — (EU) 2017/653 as amended by (EU) 2021/2268

- **Summary Risk Indicator.** Category 2 Market Risk Measure from the
  Cornish-Fisher VaR of Annex II point 12, converted to a VaR-equivalent
  volatility and banded into classes 1–7; Credit Risk Measure from credit
  quality steps with the maturity adjustment; combined through the Annex II
  point 52 matrix.
- **Performance scenarios.** Both methodologies. The 2021 approach selects the
  worst, median and best overlapping sub-period from the return history; the
  2017 approach expands the moments of the distribution. The stress scenario
  uses a stressed volatility drawn from the tail of the rolling-volatility
  distribution.
- **Costs.** Reduction in yield at one year, the mid-point and the recommended
  holding period, split by cost category, with the parts reconciling to the
  total.
- **A rendered KID** as a self-contained HTML page.

### EET and EMT validation — SFDR, EU Taxonomy and MiFID II

Four layers, in the order a data-operations team hits them:

1. **Structure** — missing mandatory columns, unrecognised columns, duplicate
   share classes.
2. **Field level** — mandatory and *conditionally* mandatory fields, value
   domains, numeric bounds, ISO dates and currencies, LEI format, and ISINs
   checked against their ISO 6166 check digit.
3. **Cross field** — the consistency rules that give a template meaning. A
   taxonomy-aligned share cannot exceed the sustainable-investment share it is a
   subset of. An Article 6 product cannot satisfy a client's sustainability
   preference. A product in risk class 6 is not a capital-preservation product.
4. **Cross template** — the EET reconciled against the EMT for the same share
   class, which is where most real breaks appear, because the two files are
   produced by different teams on different cycles.

Specs are **JSON, not code**, so a new template version is a data change:

```json
{
  "rule_id": "EET.TAXONOMY.SUBSET_OF_SUSTAINABLE",
  "title": "Taxonomy-aligned investments are a subset of sustainable investments",
  "reference": "Regulation (EU) 2020/852 Art. 3",
  "when": { "all": [
    { "field": "20030_Taxonomy_Aligned_Minimum_Share", "present": true },
    { "field": "20020_Sustainable_Investment_Minimum_Share", "present": true }
  ]},
  "then": {
    "field": "20030_Taxonomy_Aligned_Minimum_Share",
    "lte_field": "20020_Sustainable_Investment_Minimum_Share"
  }
}
```

## Install

Python 3.10 or later. Nothing else.

```bash
git clone https://github.com/gitshiven/Fund-regdata-toolkit.git
```

```bash
cd Fund-regdata-toolkit && pip install -e .
```

## Use it from the command line

```bash
fundreg ucits data/sample_portfolio.csv --html out/ucits.html
```

```
UCITS limit review: Meridian European Equity Fund
  NAV 100,000,000 EUR  valuation 2026-09-30  23 positions
  Global exposure 27.00% of NAV (gross 33.00%, netting benefit 6.00%)

  [FAIL] UCITS.ART52.1A.SINGLE_ISSUER      12.50 /  10.00  Acme Industrial SA (ACME)
         12.50% exceeds the 10.00% limit
  [FAIL] UCITS.ART56.INFLUENCE             11.50 /  10.00  Acme Industrial SA ordinary shares
         11.50% of issue exceeds the 10.00% limit
  [WARN] UCITS.ART52.2.AGGREGATE_40        38.20 /  40.00  Meridian European Equity Fund
         38.20% is within 1.80 points of the 40.00% limit
  [WARN] UCITS.ART83.BORROWING              9.50 /  10.00  Meridian European Equity Fund

  47 rules evaluated: 33 pass, 2 warning, 2 breach, 10 inconclusive
```

The Acme breach is the look-through working: 11.5% of NAV in the shares plus a
1% single-stock future is 12.5% against a 10% limit.

```bash
fundreg priips data/returns/meridian_european_equity.csv --rhp 5 --management-fee 1.05 --html out/kid.html
```

```bash
fundreg eet data/sample_eet.csv --html out/eet.html
```

```bash
fundreg reconcile data/sample_eet.csv data/sample_emt.csv
```

Exit codes follow the convention a pipeline expects: `0` clean, `1` something
breached, `2` a usage or input error. Every command takes `--html`, `--json` and
`--all` (which shows passing rules too).

## Use it as a library

```python
from fundreg import load_portfolio, load_returns
from fundreg.ucits import UcitsConfig, check_portfolio
from fundreg.priips import ProductInfo, CostStructure, build_kid

findings = check_portfolio(
    load_portfolio("data/sample_portfolio.csv"),
    UcitsConfig(single_issuer_limit=5.0, warning_ratio=0.85),
)
for f in findings.breaches:
    print(f.rule_id, f.reference, f.subject, f.actual, "vs", f.limit)

kid = build_kid(
    ProductInfo(name="Fund A", isin="LU1234567AB9",
                manufacturer="Example Management", rhp_years=5),
    load_returns("data/returns/meridian_european_equity.csv"),
    CostStructure(management_fee=0.0105, transaction_cost=0.0018),
)
print(kid.sri.sri, kid.rhp_scenarios["moderate"].value)
```

`UcitsConfig` exists because several UCITS limits are options exercised by the
home Member State or written into the prospectus. A compliance engine that
hard-codes the 5% single-issuer limit is wrong for most funds.

## Repository layout

```
src/fundreg/
  models.py            Instruments, positions, portfolios, loaders
  findings.py          The finding vocabulary every engine shares
  stats.py             Moments, inverse normal CDF, Cornish-Fisher
  ucits/
    limits.py          The limit suite
    exposure.py        Commitment approach and counterparty exposure
  priips/
    sri.py             Market and credit risk measures, the SRI matrix
    scenarios.py       Both performance-scenario methodologies
    costs.py           Reduction in yield
    kid.py             KID assembly and HTML rendering
  templates/
    spec.py            Spec model, condition language, value checking
    validator.py       Four validation layers
    report.py          HTML findings report
    specs/*.json       Bundled EET and EMT definitions
data/                  Sample portfolio, return histories, EET and EMT files
scripts/               Sample data generator and the site builder
docs/                  The published dashboard
tests/                 137 unit tests
```

## Develop

```bash
python -m unittest discover -s tests -v
```

```bash
python scripts/generate_sample_data.py && python scripts/build_site.py
```

```bash
python -m http.server -d docs 8000
```

The sample data is deterministic — a seeded GARCH process with compensated
downward jumps, so the skewness and excess kurtosis terms in the PRIIPs formulas
have something to work on — and the same seeds give the same files. The tests
assert the published numbers, so a change in methodology shows up as a failing
test rather than a silently different dashboard.

## Honest limits

This is a demonstration of methodology, not a filing system.

- **The EET and EMT specs are an illustrative reconstruction.** Field codes
  follow the numbering convention and the semantics follow the underlying
  regulations, but the authoritative field catalogue is published by
  [FinDatEx](https://findatex.eu/) and should be used for production reporting.
  Swap in a different JSON spec and the engine works unchanged.
- **PRIIPs Category 1 and 3 products are not implemented.** The SRI here covers
  Category 2, which is where most funds sit. Structured products and those with
  unusual payoffs need the Monte Carlo and bootstrapping routes of Annex II.
- **The stress scenario is as brutal as the regulation makes it.** At a seven
  year holding period the sample climate fund shows a stress value of EUR 559 on
  a EUR 10,000 investment. That is the formula, not a bug, and it is a known
  criticism of Annex IV.
- **The historical scenarios inherit the sample.** When a ten year history
  contains no bad seven year window, the "unfavourable" scenario is a gain. That
  is a real property of the 2021 methodology, and a reason the KID carries the
  sentence about past results.
- **AIFMD Annex IV reporting is not here yet.** The portfolio model was built
  with it in mind; it is the obvious next module.
- **Nothing here is legal or investment advice.** Verify against the current
  consolidated text before relying on any of it.

## Sources

- Directive 2009/65/EC (UCITS), Arts. 50–57 and 83
- CESR/10-788 — global exposure and counterparty risk for UCITS
- Commission Delegated Regulation (EU) 2017/653 (PRIIPs RTS), Annexes I, II, IV and VI
- Commission Delegated Regulation (EU) 2021/2268 amending the PRIIPs RTS
- Regulation (EU) 2019/2088 (SFDR) and Delegated Regulation (EU) 2022/1288
- Regulation (EU) 2020/852 (Taxonomy) and Delegated Regulation (EU) 2021/2178
- Directive 2014/65/EU (MiFID II), Delegated Regulation (EU) 2017/565 Art. 50,
  Delegated Regulation (EU) 2021/1253, and ESMA35-43-620 on product governance

## Licence

MIT. See [LICENSE](LICENSE).
