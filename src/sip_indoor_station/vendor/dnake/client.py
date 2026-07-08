from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from sip_indoor_station.vendor.dnake.errors import (
    DnakeAuthError,
    DnakeConnectionError,
    DnakeResponseError,
)
from sip_indoor_station.vendor.dnake.models import DnakeApiClientConfig, DnakeResponse

LOGGER = logging.getLogger(__name__)


class DnakeApiClient:
    def __init__(self, config: DnakeApiClientConfig) -> None:
        self.config = config

    @property
    def base_url(self) -> str:
        scheme = "https" if self.config.use_https else "http"
        return f"{scheme}://{self.config.host}:{self.config.port}"

    @property
    def configured(self) -> bool:
        return bool(self.config.host and self.config.username and self.config.password)

    async def get(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
        expect_json: bool = False,
    ) -> DnakeResponse:
        return await self.request("GET", path, params=params, expect_json=expect_json)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        expect_json: bool = False,
    ) -> DnakeResponse:
        if not self.configured:
            raise DnakeConnectionError("DNAKE host, username, and password must be configured")
        url = self.url(path)

        LOGGER.debug("dnake_request method=%s url=%s", method, url)
        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
        connector = aiohttp.TCPConnector(ssl=self.config.verify_ssl)

        try:
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.request(method, url, params=params) as response:
                    text = await response.text()
                    self.raise_for_status(response.status, text, path)
                    parsed_json = self.parse_json(text, path) if expect_json else None
                    return DnakeResponse(response.status, text, parsed_json)
        except (DnakeAuthError, DnakeResponseError):
            raise
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise DnakeConnectionError(f"DNAKE request timed out: {path}") from exc
        except aiohttp.ClientError as exc:
            raise DnakeConnectionError(f"DNAKE connection failed for {path}: {exc}") from exc

    def url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    @staticmethod
    def raise_for_status(status: int, body: str, path: str) -> None:
        if status in {401, 403}:
            raise DnakeAuthError(f"DNAKE authentication failed for {path}: HTTP {status}")
        if not 200 <= status < 300:
            body_preview = body[:200].replace("\r", " ").replace("\n", " ")
            raise DnakeResponseError(f"DNAKE request failed for {path}: HTTP {status}: {body_preview}")

    @staticmethod
    def parse_json(text: str, path: str) -> Any:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise DnakeResponseError(f"DNAKE returned invalid JSON for {path}") from exc
