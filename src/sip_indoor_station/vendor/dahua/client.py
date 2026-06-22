from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from sip_indoor_station.vendor.dahua.errors import (
    DahuaAuthError,
    DahuaConnectionError,
    DahuaResponseError,
)
from sip_indoor_station.vendor.dahua.models import DahuaApiClientConfig, DahuaResponse, DahuaSnapshotResponse

LOGGER = logging.getLogger(__name__)


class DahuaApiClient:
    def __init__(self, config: DahuaApiClientConfig) -> None:
        self.config = config

    @property
    def base_url(self) -> str:
        scheme = "https" if self.config.use_https else "http"
        return f"{scheme}://{self.config.host}:{self.config.port}"

    @property
    def configured(self) -> bool:
        return bool(self.config.host and self.config.username and self.config.password)

    async def get_snapshot(self, channel: int = 1) -> DahuaSnapshotResponse:
        if channel < 1:
            raise ValueError("channel must be at least 1")
        path = "/cgi-bin/snapshot.cgi"
        if not self.configured:
            raise DahuaConnectionError("Dahua host, username, and password must be configured")
        url = self.url(path)
        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
        connector = aiohttp.TCPConnector(ssl=self.config.verify_ssl)
        middlewares = (aiohttp.DigestAuthMiddleware(self.config.username or "", self.config.password or ""),)

        LOGGER.debug("dahua_snapshot_request method=%s url=%s", "GET", url)
        try:
            async with aiohttp.ClientSession(timeout=timeout, connector=connector, middlewares=middlewares) as session:
                async with session.get(url, params={"channel": channel}) as response:
                    content = await response.read()
                    self.raise_for_status(response.status, content, path)
                    content_type = response.headers.get("Content-Type")
                    return DahuaSnapshotResponse(response.status, content, content_type=content_type)
        except (DahuaAuthError, DahuaResponseError):
            raise
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise DahuaConnectionError(f"Dahua request timed out: {path}") from exc
        except aiohttp.ClientError as exc:
            raise DahuaConnectionError(f"Dahua connection failed for {path}: {exc}") from exc

    async def get(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
        expect_json: bool = False,
    ) -> DahuaResponse:
        return await self.request("GET", path, params=params, expect_json=expect_json)

    async def post(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
        json_payload: dict[str, Any] | None = None,
        expect_json: bool = False,
    ) -> DahuaResponse:
        return await self.request("POST", path, params=params, json_payload=json_payload, expect_json=expect_json)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        json_payload: dict[str, Any] | None = None,
        expect_json: bool = False,
    ) -> DahuaResponse:
        if not self.configured:
            raise DahuaConnectionError("Dahua host, username, and password must be configured")
        url = self.url(path)
        headers: dict[str, str] = {}
        json_body: dict[str, Any] | None = None
        if json_payload is not None:
            headers["Content-Type"] = "application/json"
            json_body = json_payload

        LOGGER.debug("dahua_request method=%s url=%s", method, url)
        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
        connector = aiohttp.TCPConnector(ssl=self.config.verify_ssl)
        middlewares = (aiohttp.DigestAuthMiddleware(self.config.username or "", self.config.password or ""),)

        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                middlewares=middlewares,
            ) as session:
                async with session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                ) as response:
                    text = await response.text()
                    self.raise_for_status(response.status, text, path)
                    parsed_json = self.parse_json(text, path) if expect_json else None
                    return DahuaResponse(response.status, text, parsed_json)
        except (DahuaAuthError, DahuaResponseError):
            raise
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise DahuaConnectionError(f"Dahua request timed out: {path}") from exc
        except aiohttp.ClientError as exc:
            raise DahuaConnectionError(f"Dahua connection failed for {path}: {exc}") from exc

    def url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    @staticmethod
    def raise_for_status(status: int, body: object, path: str) -> None:
        if status in {401, 403}:
            raise DahuaAuthError(f"Dahua authentication failed for {path}: HTTP {status}")
        if not 200 <= status < 300:
            body_preview = _to_body_preview(body)
            raise DahuaResponseError(f"Dahua request failed for {path}: HTTP {status}: {body_preview}")

    @staticmethod
    def parse_json(text: str, path: str) -> Any:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise DahuaResponseError(f"Dahua returned invalid JSON for {path}") from exc


def _to_body_preview(body: object) -> str:
    if isinstance(body, bytes):
        text = body[:200].decode("utf-8", errors="replace")
    elif isinstance(body, str):
        text = body[:200]
    else:
        text = str(body)[:200]
    return text.replace("\r", " ").replace("\n", " ")
