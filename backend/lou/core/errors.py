"""Typed errors used at application boundaries."""


class LouError(Exception):
    """Base exception for expected Lou failures."""


class AnalysisNotImplementedError(LouError):
    """Raised until the analysis application service is integrated."""
