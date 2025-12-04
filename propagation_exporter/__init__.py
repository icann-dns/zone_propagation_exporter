"""Propagation exporter package.

Provides journal parsing, DNS checks, metrics, and CLI entry point.
"""
# noqa: F401
from .dns_utils import DNSChecker
from .zone import ZoneConfig, ZoneInfo, ZoneManager

# Explicit public API for this package
__all__ = [
    "DNSChecker",
    "ZoneManager",
    "ZoneInfo",
    "ZoneConfig",
]

# Package version
__version__ = "0.8.0"
