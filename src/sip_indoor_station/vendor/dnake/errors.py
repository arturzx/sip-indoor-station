from __future__ import annotations


class DnakeApiError(Exception):
    """Base exception for DNAKE API failures."""


class DnakeAuthError(DnakeApiError):
    """Authentication or authorization failed."""


class DnakeConnectionError(DnakeApiError):
    """Connection, DNS, or timeout failure."""


class DnakeResponseError(DnakeApiError):
    """The device returned an unexpected response."""
