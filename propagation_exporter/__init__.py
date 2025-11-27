"""Propagation exporter package.

Provides journal parsing, DNS checks, metrics, and CLI entry point.
"""
# noqa: F401
from .dns_utils import DNSChecker
from .zone import (
    ZoneManager,
    DEFAULT_ZONE_SERIAL_REGEX,
    ZoneInfo,
    ZoneConfig
)

# Explicit public API for this package
__all__ = [
    "DNSChecker",
    "ZoneManager",
    "DEFAULT_ZONE_SERIAL_REGEX",
    "ZoneInfo",
    "ZoneConfig",
]
