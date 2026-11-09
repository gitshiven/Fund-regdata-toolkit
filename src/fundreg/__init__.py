"""fundreg - a regulatory toolkit for the European fund industry.

Modules
-------
fundreg.ucits      UCITS Directive 2009/65/EC investment limits and global exposure.
fundreg.priips     PRIIPs KID: summary risk indicator, performance scenarios, costs.
fundreg.templates  FinDatEx-style EET / EMT template validation (SFDR + MiFID II).
"""

__version__ = "0.1.0"

from fundreg.models import (
    AssetClass,
    Derivative,
    Instrument,
    Portfolio,
    Position,
    load_portfolio,
    load_returns,
)

__all__ = [
    "__version__",
    "AssetClass",
    "Derivative",
    "Instrument",
    "Portfolio",
    "Position",
    "load_portfolio",
    "load_returns",
]
