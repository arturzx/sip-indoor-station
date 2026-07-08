from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import aiohttp

from sip_indoor_station.sip.server import Snapshot
from sip_indoor_station.vendor.onvif.errors import (
    OnvifAuthError,
    OnvifConnectionError,
    OnvifDependencyError,
    OnvifProviderError,
    OnvifResponseError,
)
from sip_indoor_station.vendor.onvif.models import OnvifClientConfig, OnvifSnapshotResponse

LOGGER = logging.getLogger(__name__)

OnvifCameraFactory = Callable[[OnvifClientConfig], object]
SnapshotFetcher = Callable[[str], Awaitable[OnvifSnapshotResponse]]


class OnvifSnapshotProvider:
    def __init__(
        self,
        config: OnvifClientConfig,
        *,
        profile_index: int = 0,
        camera_factory: OnvifCameraFactory | None = None,
        snapshot_fetcher: SnapshotFetcher | None = None,
    ) -> None:
        self.config = config
        self.profile_index = profile_index
        self.camera_factory = camera_factory or create_onvif_camera
        self.snapshot_fetcher = snapshot_fetcher or self.fetch_snapshot
        self._snapshot_uri: str | None = None
        self._snapshot_uri_lock = asyncio.Lock()

    async def capture_snapshot(self) -> Snapshot | None:
        had_cached_uri = self._snapshot_uri is not None
        try:
            uri = await self.get_cached_snapshot_uri()
            response = await self.snapshot_fetcher(uri)
        except OnvifProviderError as exc:
            if had_cached_uri:
                LOGGER.info("onvif_snapshot_retrying_after_cached_uri_failed reason=%s", exc)
                self.clear_snapshot_uri_cache()
                return await self.capture_snapshot()
            self.clear_snapshot_uri_cache()
            LOGGER.warning("onvif_snapshot_failed reason=%s", exc)
            return None

        if not response.content:
            return None
        LOGGER.info("onvif_snapshot_success status=%s", response.status)
        return Snapshot(response.content, response.content_type or "image/jpeg")

    async def get_cached_snapshot_uri(self) -> str:
        if self._snapshot_uri is not None:
            return self._snapshot_uri
        async with self._snapshot_uri_lock:
            if self._snapshot_uri is None:
                self._snapshot_uri = await self.run_blocking(self.get_snapshot_uri)
            return self._snapshot_uri

    def clear_snapshot_uri_cache(self) -> None:
        self._snapshot_uri = None

    async def run_blocking(self, func: Callable[[], str]) -> str:
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return await loop.run_in_executor(executor, func)

    def get_snapshot_uri(self) -> str:
        try:
            media_service = self.camera_factory(self.config).create_media_service()  # type: ignore[attr-defined]
            profiles = _as_list(media_service.GetProfiles())
            if self.profile_index < 0 or self.profile_index >= len(profiles):
                raise OnvifResponseError(f"ONVIF profile index {self.profile_index} is not available")

            profile_token = _field(profiles[self.profile_index], "token") or _field(
                profiles[self.profile_index], "Token"
            )
            if not profile_token:
                raise OnvifResponseError(f"ONVIF profile index {self.profile_index} has no token")

            response = media_service.GetSnapshotUri({"ProfileToken": profile_token})
            uri = response if isinstance(response, str) else _field(response, "Uri")
            if not uri:
                raise OnvifResponseError("ONVIF GetSnapshotUri returned no Uri")
            return str(uri)
        except OnvifProviderError:
            raise
        except Exception as exc:
            raise OnvifResponseError(f"ONVIF GetSnapshotUri failed: {exc}") from exc

    async def fetch_snapshot(self, uri: str) -> OnvifSnapshotResponse:
        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
        connector = aiohttp.TCPConnector(ssl=self.config.verify_ssl)
        middlewares = ()
        if self.config.username and self.config.password:
            middlewares = (aiohttp.DigestAuthMiddleware(self.config.username, self.config.password),)

        try:
            async with aiohttp.ClientSession(timeout=timeout, connector=connector, middlewares=middlewares) as session:
                async with session.get(uri) as response:
                    content = await response.read()
                    self.raise_for_status(response.status, content, uri)
                    return OnvifSnapshotResponse(response.status, content, response.headers.get("Content-Type"))
        except (OnvifAuthError, OnvifResponseError):
            raise
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise OnvifConnectionError(f"ONVIF snapshot request timed out: {uri}") from exc
        except aiohttp.ClientError as exc:
            raise OnvifConnectionError(f"ONVIF snapshot connection failed for {uri}: {exc}") from exc

    @staticmethod
    def raise_for_status(status: int, body: bytes, uri: str) -> None:
        if status in {401, 403}:
            raise OnvifAuthError(f"ONVIF snapshot authentication failed for {uri}: HTTP {status}")
        if not 200 <= status < 300:
            body_preview = body[:200].decode("utf-8", errors="replace").replace("\r", " ").replace("\n", " ")
            raise OnvifResponseError(f"ONVIF snapshot request failed for {uri}: HTTP {status}: {body_preview}")


def create_onvif_camera(config: OnvifClientConfig) -> object:
    try:
        from onvif import ONVIFCamera
    except ImportError as exc:
        raise OnvifDependencyError("onvif-zeep is required for ONVIF snapshot support") from exc
    return ONVIFCamera(config.host, config.port, config.username or "", config.password or "")


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _field(value: object, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)
