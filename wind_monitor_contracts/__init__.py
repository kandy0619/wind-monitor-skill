"""Versioned public contract for KStock persistence integrations."""

from .v1 import (
    API_VERSION,
    CONTRACT_VERSION,
    ContractError,
    canonical_hash,
    extract_intraday_facts,
    extract_close_facts,
    extract_trend_sample_facts,
    validate_claim,
    validate_report,
)

__all__ = [
    "API_VERSION",
    "CONTRACT_VERSION",
    "ContractError",
    "canonical_hash",
    "extract_intraday_facts",
    "extract_close_facts",
    "extract_trend_sample_facts",
    "validate_claim",
    "validate_report",
]
