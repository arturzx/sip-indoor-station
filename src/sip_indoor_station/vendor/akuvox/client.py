from __future__ import annotations

import asyncio
import logging

import aiohttp

from sip_indoor_station.vendor.akuvox.errors import (
    AkuvoxAuthError,
    AkuvoxConnectionError,
    AkuvoxResponseError,
)
from sip_indoor_station.vendor.akuvox.models import AkuvoxApiClientConfig, AkuvoxResponse, AkuvoxSnapshotResponse

LOGGER = logging.getLogger(__name__)


class AkuvoxApiClient:
    def __init__(self, config: AkuvoxApiClientConfig) -> None:
        self.config = config

    @property
    def base_url(self) -> str:
        scheme = "https" if self.config.use_https else "http"
        return f"{scheme}://{self.config.host}:{self.config.port}"

    @property
    def snapshot_base_url(self) -> str:
        return f"http://{self.config.host}:8080"

    @property
    def configured(self) -> bool:
        return bool(self.config.host and self.config.username and self.config.password)

    async def get(self, path: str, *, params: dict[str, object] | None = None) -> AkuvoxResponse:
        if not self.configured:
            raise AkuvoxConnectionError("Akuvox host, username, and password must be configured")
        url = self.url(path)
        LOGGER.debug("akuvox_request method=GET url=%s", url)
        try:
            async with self._session() as session:
                async with session.get(url, params=params) as response:
                    text = await response.text()
                    self.raise_for_status(response.status, text, path)
                    return AkuvoxResponse(response.status, text)
        except (AkuvoxAuthError, AkuvoxResponseError):
            raise
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise AkuvoxConnectionError(f"Akuvox request timed out: {path}") from exc
        except aiohttp.ClientError as exc:
            raise AkuvoxConnectionError(f"Akuvox connection failed for {path}: {exc}") from exc

    async def get_snapshot(self, path: str = "/picture.jpg") -> AkuvoxSnapshotResponse:
        if not self.configured:
            raise AkuvoxConnectionError("Akuvox host, username, and password must be configured")
        url = self.snapshot_url(path)
        LOGGER.debug("akuvox_snapshot_request method=GET url=%s", url)
        try:
            async with self._session() as session:
                async with session.get(url) as response:
                    content = await response.read()
                    self.raise_for_status(response.status, content, path)
                    return AkuvoxSnapshotResponse(response.status, content, response.headers.get("Content-Type"))
        except (AkuvoxAuthError, AkuvoxResponseError):
            raise
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise AkuvoxConnectionError(f"Akuvox snapshot request timed out: {path}") from exc
        except aiohttp.ClientError as exc:
            raise AkuvoxConnectionError(f"Akuvox snapshot connection failed for {path}: {exc}") from exc

    def url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def snapshot_url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.snapshot_base_url + path

    def _session(self) -> aiohttp.ClientSession:
        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
        connector = aiohttp.TCPConnector(ssl=self.config.verify_ssl)
        middlewares = (aiohttp.DigestAuthMiddleware(self.config.username or "", self.config.password or ""),)
        return aiohttp.ClientSession(timeout=timeout, connector=connector, middlewares=middlewares)

    @staticmethod
    def raise_for_status(status: int, body: object, path: str) -> None:
        if status in {401, 403}:
            raise AkuvoxAuthError(f"Akuvox authentication failed for {path}: HTTP {status}")
        if not 200 <= status < 300:
            if isinstance(body, bytes):
                body_preview = body[:200].decode("utf-8", errors="replace")
            else:
                body_preview = str(body)[:200]
            raise AkuvoxResponseError(f"Akuvox request failed for {path}: HTTP {status}: {body_preview}")