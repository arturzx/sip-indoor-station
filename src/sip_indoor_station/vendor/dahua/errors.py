from __future__ import annotations


class DahuaApiError(Exception):
    """Base exception for Dahua API failures."""


class DahuaAuthError(DahuaApiError):
    """Authentication or authorization failed."""


class DahuaConnectionError(DahuaApiError):
    """Connection, DNS, or timeout failure."""


class DahuaResponseError(DahuaApiError):
    """The device returned an unexpected response."""
