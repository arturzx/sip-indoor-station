from __future__ import annotations


class OnvifProviderError(Exception):
    """Base exception for ONVIF provider failures."""


class OnvifAuthError(OnvifProviderError):
    """Authentication or authorization failed."""


class OnvifConnectionError(OnvifProviderError):
    """Connection, DNS, or timeout failure."""


class OnvifResponseError(OnvifProviderError):
    """The device returned an unexpected response."""
