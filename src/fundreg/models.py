"""Core domain model shared by the UCITS, PRIIPs and template engines.

Deliberately dependency-free: a position is a plain dataclass so the whole
toolkit runs on a stock CPython install with no wheels to build.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class AssetClass(str, Enum):
    """Instrument taxonomy used by the UCITS eligibility and limit tests.

    The distinction matters because Directive 2009/65/EC applies a different
    issuer limit to each family (Art. 52-56).
    """

    EQUITY = "equity"
    CORPORATE_BOND = "corporate_bond"
    GOVERNMENT_BOND = "government_bond"
    COVERED_BOND = "covered_bond"
    MONEY_MARKET_INSTRUMENT = "money_market_instrument"
    DEPOSIT = "deposit"
    UCITS_FUND = "ucits_fund"
    NON_UCITS_CIS = "non_ucits_cis"
    OTC_DERIVATIVE = "otc_derivative"
    ETD_DERIVATIVE = "etd_derivative"
    CASH = "cash"
    # Art. 50(2)(a): assets outside the Art. 50(1) eligible list, capped at 10%.
    INELIGIBLE = "ineligible"


#: Asset classes that count towards the Art. 52(1) 5/10/40 issuer concentration test.
ISSUER_LIMIT_CLASSES = frozenset(
    {
        AssetClass.EQUITY,
        AssetClass.CORPORATE_BOND,
        AssetClass.GOVERNMENT_BOND,
        AssetClass.COVERED_BOND,
        AssetClass.MONEY_MARKET_INSTRUMENT,
    }
)

#: Asset classes that are derivatives for global-exposure purposes.
DERIVATIVE_CLASSES = frozenset({AssetClass.OTC_DERIVATIVE, AssetClass.ETD_DERIVATIVE})


@dataclass(frozen=True)
class Derivative:
    """Derivative detail needed to convert a contract into its commitment.

    Attributes
    ----------
    underlying_id:
        Identifier of the underlying. Used to group positions into hedging or
        netting sets and to apply issuer look-through.
    notional:
        Market value of the equivalent position in the underlying asset, signed:
        positive for long exposure, negative for short. This is the commitment
        of CESR/10-788 Box 3 before netting.
    counterparty:
        Legal entity on the other side of the trade. Only populated for OTC
        contracts, which carry an Art. 52(1) counterparty limit.
    collateral_received:
        Value of eligible collateral received, which reduces counterparty
        exposure under the ESMA/2014/937 guidelines.
    netting_set:
        Optional label grouping contracts that may be netted. Contracts sharing
        a netting set and referencing the same underlying are summed with sign
        before the absolute value is taken.
    """

    underlying_id: str
    notional: float
    counterparty: str | None = None
    collateral_received: float = 0.0
    netting_set: str | None = None


@dataclass(frozen=True)
class Instrument:
    """A security, deposit, fund unit or derivative contract."""

    instrument_id: str
    name: str
    asset_class: AssetClass
    issuer_id: str
    issuer_name: str = ""
    #: True where the issuer is a sovereign, a public international body or an
    #: EU/EEA local authority, unlocking the Art. 52(3) 35% limit.
    is_public_body: bool = False
    #: True for Art. 52(4) covered bonds, unlocking the 25% limit.
    is_covered_bond: bool = False
    #: True where the instrument is admitted to trading on a regulated market
    #: or otherwise satisfies Art. 50(1). False routes it to the 10% trash ratio.
    is_eligible_art50: bool = True
    #: Issued share/unit/debt count, used for the Art. 56 influence limits.
    issuer_total_units: float | None = None
    #: Whether derivative exposure is attributed to a single issuer for the
    #: Art. 52 concentration tests. False for contracts on an index, a basket,
    #: an interest rate or a currency, where there is no single obligor to look
    #: through to (CESR/10-788, Box 3 and the Art. 53 index derogation).
    look_through_to_issuer: bool = True
    derivative: Derivative | None = None

    def __post_init__(self) -> None:
        if self.asset_class in DERIVATIVE_CLASSES and self.derivative is None:
            raise ValueError(
                f"{self.instrument_id}: derivative instruments require a Derivative payload"
            )


@dataclass(frozen=True)
class Position:
    """A holding of an :class:`Instrument` with its mark-to-market value."""

    instrument: Instrument
    #: Number of shares, units or contracts. Negative for short positions.
    quantity: float
    #: Signed mark-to-market value of the holding in the base currency of the fund.
    market_value: float

    @property
    def asset_class(self) -> AssetClass:
        return self.instrument.asset_class

    @property
    def issuer_id(self) -> str:
        return self.instrument.issuer_id


@dataclass
class Portfolio:
    """A fund portfolio at a single valuation point."""

    fund_name: str
    nav: float
    base_currency: str = "EUR"
    valuation_date: str = ""
    positions: list[Position] = field(default_factory=list)
    #: Cash borrowed under Art. 83(2)(b), capped at 10% of NAV on a temporary basis.
    borrowing: float = 0.0

    def __iter__(self) -> Iterator[Position]:
        return iter(self.positions)

    def __len__(self) -> int:
        return len(self.positions)

    def add(self, position: Position) -> None:
        self.positions.append(position)

    def pct(self, value: float) -> float:
        """Express ``value`` as a percentage of NAV."""
        if self.nav == 0:
            raise ZeroDivisionError("NAV is zero; percentages are undefined")
        return 100.0 * value / self.nav

    def of_class(self, *classes: AssetClass) -> list[Position]:
        wanted = set(classes)
        return [p for p in self.positions if p.asset_class in wanted]

    def total_value(self, positions: Iterable[Position] | None = None) -> float:
        source = positions if positions is not None else self.positions
        return sum(p.market_value for p in source)


def load_portfolio(path: str | Path) -> Portfolio:
    """Read a portfolio from the CSV format of the toolkit.

    The header carries fund-level data as ``#key=value`` comment lines so that a
    portfolio travels as a single file. Everything after the comments is a plain
    CSV with one row per position.
    """

    path = Path(path)
    meta: dict[str, str] = {}
    rows: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                key, _, value = line[1:].strip().partition("=")
                meta[key.strip()] = value.strip()
            elif line.strip():
                rows.append(line)

    portfolio = Portfolio(
        fund_name=meta.get("fund_name", path.stem),
        nav=float(meta.get("nav", 0.0)),
        base_currency=meta.get("base_currency", "EUR"),
        valuation_date=meta.get("valuation_date", ""),
        borrowing=float(meta.get("borrowing", 0.0)),
    )

    for row in csv.DictReader(rows):
        derivative = None
        if row.get("underlying_id"):
            derivative = Derivative(
                underlying_id=row["underlying_id"],
                notional=float(row.get("notional") or 0.0),
                counterparty=row.get("counterparty") or None,
                collateral_received=float(row.get("collateral_received") or 0.0),
                netting_set=row.get("netting_set") or None,
            )
        instrument = Instrument(
            instrument_id=row["instrument_id"],
            name=row.get("name", row["instrument_id"]),
            asset_class=AssetClass(row["asset_class"]),
            issuer_id=row["issuer_id"],
            issuer_name=row.get("issuer_name", row["issuer_id"]),
            is_public_body=_as_bool(row.get("is_public_body")),
            is_covered_bond=_as_bool(row.get("is_covered_bond")),
            is_eligible_art50=_as_bool(row.get("is_eligible_art50"), default=True),
            issuer_total_units=_as_float(row.get("issuer_total_units")),
            look_through_to_issuer=_as_bool(row.get("look_through_to_issuer"), default=True),
            derivative=derivative,
        )
        portfolio.add(
            Position(
                instrument=instrument,
                quantity=float(row.get("quantity") or 0.0),
                market_value=float(row["market_value"]),
            )
        )
    return portfolio


def load_returns(path: str | Path) -> list[float]:
    """Load a return series from a one-column CSV (header ``return``) or JSON list."""

    path = Path(path)
    if path.suffix.lower() == ".json":
        return [float(x) for x in json.loads(path.read_text(encoding="utf-8"))]
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or [""]
        column = "return" if "return" in fields else fields[0]
        return [float(row[column]) for row in reader if row[column]]


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _as_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
