"""UCITS investment limits and global exposure.

Implements the diversification, concentration and leverage rules of Directive
2009/65/EC (Arts. 50-57 and 83) together with the commitment-approach global
exposure calculation of CESR/10-788.
"""

from fundreg.ucits.exposure import (
    CommitmentResult,
    commitment_approach_exposure,
    counterparty_exposures,
)
from fundreg.ucits.limits import UcitsConfig, check_portfolio

__all__ = [
    "CommitmentResult",
    "UcitsConfig",
    "check_portfolio",
    "commitment_approach_exposure",
    "counterparty_exposures",
]
