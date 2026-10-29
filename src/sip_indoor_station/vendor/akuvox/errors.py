from __future__ import annotations


class AkuvoxApiError(Exception):
    """Base exception for Akuvox API failures."""


class AkuvoxAuthError(AkuvoxApiError):
    """Authentication or authorization failed."""


class AkuvoxConnectionError(AkuvoxApiError):
    """Connection, DNS, or timeout failure."""


class AkuvoxResponseError(AkuvoxApiError):
    """The device returned an unexpected response."""
