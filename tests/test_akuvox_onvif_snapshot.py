from __future__ import annotations

import asyncio

from sip_indoor_station.app.config import Config, SipUser
from sip_indoor_station.vendor.onvif.errors import OnvifAuthError
from sip_indoor_station.vendor.onvif.models import OnvifClientConfig, OnvifSnapshotResponse
from sip_indoor_station.vendor.onvif.snapshot import OnvifSnapshotProvider


def _make_config(**kwargs) -> Config:
    return Config(
        sip_realm="sip.local",
        sip_users={"door": SipUser(username="door", password="secret", realm="sip.local")},
        **kwargs,
    )


class _FakeOnvifClient:
    async def get_profiles(self):
        return [{"token": "Profile_1"}]

    async def get_snapshot_uri(self, profile_token: str) -> str:
        return f"http://192.168.1.100/snapshot.jpg?token={profile_token}"


_JPEG = b"\xff\xd8\xff\xe0fake_jpeg_data"


async def _fetch_ok(uri: str) -> OnvifSnapshotResponse:
    return OnvifSnapshotResponse(status=200, content=_JPEG, content_type="image/jpeg")


async def _fetch_auth_fail(uri: str) -> OnvifSnapshotResponse:
    raise OnvifAuthError(f"ONVIF snapshot authentication failed: HTTP 401")


def _run_provider(provider: OnvifSnapshotProvider):
    return asyncio.run(provider.capture_snapshot())


def test_onvif_snapshot_returned():
    cfg = OnvifClientConfig(host="192.168.1.100", username="admin", password="pass")
    provider = OnvifSnapshotProvider(
        cfg,
        profile_index=0,
        client_factory=lambda _: _FakeOnvifClient(),
        snapshot_fetcher=_fetch_ok,
    )
    snapshot = _run_provider(provider)
    assert snapshot is not None
    assert snapshot.content == _JPEG
    assert snapshot.content_type == "image/jpeg"


def test_onvif_snapshot_returns_none_on_auth_failure():
    cfg = OnvifClientConfig(host="192.168.1.100", username="admin", password="wrong")
    provider = OnvifSnapshotProvider(
        cfg,
        profile_index=0,
        client_factory=lambda _: _FakeOnvifClient(),
        snapshot_fetcher=_fetch_auth_fail,
    )
    snapshot = _run_provider(provider)
    assert snapshot is None


def test_config_onvif_defaults():
    cfg = _make_config()
    assert cfg.onvif_port == 80
    assert cfg.onvif_profile_index == 0


def test_config_onvif_port_and_profile_set():
    cfg = _make_config(onvif_port=8080, onvif_profile_index=1)
    assert cfg.onvif_port == 8080
    assert cfg.onvif_profile_index == 1


def test_akuvox_onvif_uses_api_host_credentials():
    """ONVIF for Akuvox reuses api_host/api_username/api_password — no separate onvif credentials."""
    cfg = _make_config(
        door_station_vendor="akuvox",
        api_host="192.168.1.100",
        api_username="admin",
        api_password="secret",
        onvif_port=80,
        onvif_profile_index=0,
    )
    assert cfg.api_host == "192.168.1.100"
    assert cfg.api_username == "admin"
    assert cfg.onvif_port == 80
