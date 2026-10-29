from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from os import urandom
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp

from sip_indoor_station.vendor.akuvox.errors import (
    AkuvoxAuthError,
    AkuvoxConnectionError,
    AkuvoxResponseError,
)
from sip_indoor_station.vendor.akuvox.models import AkuvoxApiClientConfig, AkuvoxResponse

LOGGER = logging.getLogger(__name__)


class AkuvoxApiClient:
    """Client for the Akuvox HTTP API (none/allowlist, Basic and Digest auth)."""

    def __init__(self, config: AkuvoxApiClientConfig) -> None:
        self.config = config

    @property
    def base_url(self) -> str:
        scheme = "https" if self.config.use_https else "http"
        return f"{scheme}://{self.config.host}:{self.config.port}"

    @property
    def configured(self) -> bool:
        return bool(self.config.host)

    async def get(
        self,
        path: str,
        *,
        params: dict[str, object] | None = None,
        credentials: tuple[str, str] | None = None,
        expect_retcode: bool = True,
    ) -> AkuvoxResponse:
        """Perform a GET; by default require the API-level retcode to be 0."""
        if credentials is None and self.config.username and self.config.password:
            credentials = (self.config.username, self.config.password)
        if not self.configured:
            raise AkuvoxConnectionError("Akuvox host must be configured")
        url = self.url(path)
        LOGGER.debug("akuvox_request url=%s", url)
        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
        connector = aiohttp.TCPConnector(ssl=self.config.verify_ssl)
        try:
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                status, text = await self._get_with_auth(session, url, params, credentials)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise AkuvoxConnectionError(f"Akuvox request timed out: {path}") from exc
        except aiohttp.ClientError as exc:
            raise AkuvoxConnectionError(f"Akuvox connection failed for {path}: {exc}") from exc
        self.raise_for_status(status, text, path)
        if not expect_retcode:
            return AkuvoxResponse(status, text, None)
        data = self.parse_json(text, path)
        retcode = data.get("retcode") if isinstance(data, dict) else None
        if retcode not in (0, "0"):
            message = data.get("message") if isinstance(data, dict) else None
            raise AkuvoxResponseError(f"Akuvox request failed for {path}: retcode={retcode} message={message}")
        return AkuvoxResponse(status, text, data)

    async def _get_with_auth(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: dict[str, object] | None,
        credentials: tuple[str, str] | None,
        redirects: int = 2,
    ) -> tuple[int, str]:
        async with session.get(url, params=params) as response:
            status, text = response.status, await response.text()
            challenge = response.headers.get("WWW-Authenticate", "")
            final_url = str(response.url)
        if status != 401 or credentials is None:
            return status, text
        scheme = challenge.split(None, 1)[0].lower() if challenge else ""
        if scheme == "basic":
            headers = {"Authorization": aiohttp.BasicAuth(*credentials).encode()}
        elif scheme == "digest":
            headers = {"Authorization": self.digest_header(challenge, "GET", urlsplit(final_url), credentials)}
        else:
            return status, text
        async with session.get(final_url, headers=headers, allow_redirects=False) as response:
            location = response.headers.get("Location")
            if response.status in {301, 302, 307, 308} and location and redirects > 0:
                # Some devices only accept authenticated requests over HTTPS and redirect there;
                # redirects would drop the credentials, so redo the handshake on the new URL.
                target = urljoin(final_url, location)
                if not urlsplit(target).query and urlsplit(final_url).query:
                    target += "?" + urlsplit(final_url).query
            else:
                return response.status, await response.text()
        return await self._get_with_auth(session, target, None, credentials, redirects - 1)

    def digest_header(self, challenge: str, method: str, parts: Any, credentials: tuple[str, str]) -> str:
        username, password = credentials
        fields = self.parse_challenge(challenge)
        realm = fields.get("realm", "")
        nonce = fields.get("nonce", "")
        qop = "auth" if "auth" in fields.get("qop", "").split(",") or "auth" in fields.get("qop", "") else ""
        uri = parts.path + (f"?{parts.query}" if parts.query else "")
        ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
        cnonce = urandom(8).hex()
        if qop:
            response = hashlib.md5(f"{ha1}:{nonce}:00000001:{cnonce}:{qop}:{ha2}".encode()).hexdigest()
        else:
            response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
        header = (
            f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
            f'uri="{uri}", response="{response}"'
        )
        if qop:
            header += f', qop={qop}, nc=00000001, cnonce="{cnonce}"'
        if "opaque" in fields:
            header += f', opaque="{fields["opaque"]}"'
        return header

    @staticmethod
    def parse_challenge(challenge: str) -> dict[str, str]:
        fields: dict[str, str] = {}
        body = challenge.split(None, 1)[1] if " " in challenge.strip() else ""
        for item in _split_params(body):
            key, _, value = item.partition("=")
            fields[key.strip().lower()] = value.strip().strip('"')
        return fields

    def url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    @staticmethod
    def raise_for_status(status: int, body: str, path: str) -> None:
        if status in {401, 403}:
            raise AkuvoxAuthError(f"Akuvox authentication failed for {path}: HTTP {status}")
        if not 200 <= status < 300:
            preview = body[:200].replace("\r", " ").replace("\n", " ")
            raise AkuvoxResponseError(f"Akuvox request failed for {path}: HTTP {status}: {preview}")

    @staticmethod
    def parse_json(text: str, path: str) -> Any:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AkuvoxResponseError(f"Akuvox returned invalid JSON for {path}") from exc


def _split_params(body: str) -> list[str]:
    items: list[str] = []
    current = ""
    quoted = False
    for char in body:
        if char == '"':
            quoted = not quoted
        if char == "," and not quoted:
            items.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        items.append(current)
    return items
