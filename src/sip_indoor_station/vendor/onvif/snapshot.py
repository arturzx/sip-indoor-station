from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from os import urandom
from typing import Any, Protocol
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import aiohttp

from sip_indoor_station.sip.server import Snapshot
from sip_indoor_station.vendor.onvif.errors import (
    OnvifAuthError,
    OnvifConnectionError,
    OnvifProviderError,
    OnvifResponseError,
)
from sip_indoor_station.vendor.onvif.models import OnvifClientConfig, OnvifSnapshotResponse

LOGGER = logging.getLogger(__name__)

SOAP_ENV_NS = "http://www.w3.org/2003/05/soap-envelope"
DEVICE_NS = "http://www.onvif.org/ver10/device/wsdl"
MEDIA_NS = "http://www.onvif.org/ver10/media/wsdl"
WSSE_NS = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-wssecurity-secext-1.0.xsd"
)
WSU_NS = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-wssecurity-utility-1.0.xsd"
)
PASSWORD_DIGEST_TYPE = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
)
BASE64_ENCODING_TYPE = (
    "http://docs.oasis-open.org/wss/2004/01/"
    "oasis-200401-wss-soap-message-security-1.0#Base64Binary"
)


class OnvifSnapshotUriClient(Protocol):
    async def get_profiles(self) -> object:
        ...

    async def get_snapshot_uri(self, profile_token: str) -> str:
        ...


OnvifClientFactory = Callable[[OnvifClientConfig], OnvifSnapshotUriClient]
SnapshotFetcher = Callable[[str], Awaitable[OnvifSnapshotResponse]]


class OnvifSnapshotProvider:
    def __init__(
        self,
        config: OnvifClientConfig,
        *,
        profile_index: int = 0,
        client_factory: OnvifClientFactory | None = None,
        snapshot_fetcher: SnapshotFetcher | None = None,
    ) -> None:
        self.config = config
        self.profile_index = profile_index
        self.client_factory = client_factory or LightweightOnvifClient
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
                self._snapshot_uri = await self.get_snapshot_uri()
            return self._snapshot_uri

    def clear_snapshot_uri_cache(self) -> None:
        self._snapshot_uri = None

    async def get_snapshot_uri(self) -> str:
        try:
            client = self.client_factory(self.config)
            profiles = _as_list(await client.get_profiles())
            if self.profile_index < 0 or self.profile_index >= len(profiles):
                raise OnvifResponseError(f"ONVIF profile index {self.profile_index} is not available")

            profile_token = _field(profiles[self.profile_index], "token") or _field(
                profiles[self.profile_index], "Token"
            )
            if not profile_token:
                raise OnvifResponseError(f"ONVIF profile index {self.profile_index} has no token")

            uri = await client.get_snapshot_uri(str(profile_token))
            if not uri:
                raise OnvifResponseError("ONVIF GetSnapshotUri returned no Uri")
            return uri
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
                    raise_for_status(response.status, content, uri, "snapshot")
                    return OnvifSnapshotResponse(response.status, content, response.headers.get("Content-Type"))
        except (OnvifAuthError, OnvifResponseError):
            raise
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise OnvifConnectionError(f"ONVIF snapshot request timed out: {uri}") from exc
        except aiohttp.ClientError as exc:
            raise OnvifConnectionError(f"ONVIF snapshot connection failed for {uri}: {exc}") from exc


class LightweightOnvifClient:
    def __init__(self, config: OnvifClientConfig) -> None:
        self.config = config
        self._media_service_url: str | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.config.host}:{self.config.port}"

    @property
    def device_service_url(self) -> str:
        return self.url("/onvif/device_service")

    @property
    def default_media_service_url(self) -> str:
        return self.url("/onvif/media_service")

    def url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    async def get_profiles(self) -> list[dict[str, str]]:
        media_service_url = await self.get_media_service_url()
        root = await self.soap_request(
            media_service_url,
            "GetProfiles",
            f"{MEDIA_NS}/GetProfiles",
            "<trt:GetProfiles/>",
        )
        profiles: list[dict[str, str]] = []
        for element in root.iter():
            if _local_name(element.tag) == "Profiles":
                token = _attribute(element, "token") or _attribute(element, "Token")
                if token:
                    profiles.append({"token": token})
        return profiles

    async def get_snapshot_uri(self, profile_token: str) -> str:
        media_service_url = await self.get_media_service_url()
        root = await self.soap_request(
            media_service_url,
            "GetSnapshotUri",
            f"{MEDIA_NS}/GetSnapshotUri",
            (
                "<trt:GetSnapshotUri>"
                f"<trt:ProfileToken>{escape(profile_token)}</trt:ProfileToken>"
                "</trt:GetSnapshotUri>"
            ),
        )
        uri = _first_text(root, "Uri")
        if not uri:
            raise OnvifResponseError("ONVIF GetSnapshotUri returned no Uri")
        return uri

    async def get_media_service_url(self) -> str:
        if self._media_service_url is not None:
            return self._media_service_url
        try:
            root = await self.soap_request(
                self.device_service_url,
                "GetCapabilities",
                f"{DEVICE_NS}/GetCapabilities",
                "<tds:GetCapabilities><tds:Category>Media</tds:Category></tds:GetCapabilities>",
            )
            xaddr = _first_text(root, "XAddr")
        except OnvifProviderError as exc:
            LOGGER.debug("onvif_capabilities_failed_using_default_media_service reason=%s", exc)
            xaddr = None
        self._media_service_url = self.url(xaddr) if xaddr else self.default_media_service_url
        return self._media_service_url

    async def soap_request(
        self,
        url: str,
        operation: str,
        action: str,
        body: str,
    ) -> ElementTree.Element:
        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
        connector = aiohttp.TCPConnector(ssl=self.config.verify_ssl)
        middlewares = ()
        if self.config.username and self.config.password:
            middlewares = (aiohttp.DigestAuthMiddleware(self.config.username, self.config.password),)
        headers = {
            "Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"',
            "SOAPAction": f'"{action}"',
        }

        try:
            async with aiohttp.ClientSession(timeout=timeout, connector=connector, middlewares=middlewares) as session:
                async with session.post(url, data=self.soap_envelope(body).encode(), headers=headers) as response:
                    content = await response.read()
                    raise_for_status(response.status, content, url, operation)
                    return parse_soap_response(content, operation)
        except (OnvifAuthError, OnvifResponseError):
            raise
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise OnvifConnectionError(f"ONVIF {operation} request timed out: {url}") from exc
        except aiohttp.ClientError as exc:
            raise OnvifConnectionError(f"ONVIF {operation} connection failed for {url}: {exc}") from exc

    def soap_envelope(self, body: str) -> str:
        return (
            f'<s:Envelope xmlns:s="{SOAP_ENV_NS}" xmlns:tds="{DEVICE_NS}" '
            f'xmlns:trt="{MEDIA_NS}" xmlns:wsse="{WSSE_NS}" xmlns:wsu="{WSU_NS}">'
            f"{self.wsse_header()}"
            f"<s:Body>{body}</s:Body>"
            "</s:Envelope>"
        )

    def wsse_header(self) -> str:
        if not self.config.username or not self.config.password:
            return ""
        nonce = urandom(16)
        created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        password_digest = base64.b64encode(
            hashlib.sha1(nonce + created.encode() + self.config.password.encode()).digest()
        ).decode()
        nonce_text = base64.b64encode(nonce).decode()
        return (
            '<s:Header><wsse:Security s:mustUnderstand="true">'
            "<wsse:UsernameToken>"
            f"<wsse:Username>{escape(self.config.username)}</wsse:Username>"
            f'<wsse:Password Type="{PASSWORD_DIGEST_TYPE}">{password_digest}</wsse:Password>'
            f'<wsse:Nonce EncodingType="{BASE64_ENCODING_TYPE}">{nonce_text}</wsse:Nonce>'
            f"<wsu:Created>{created}</wsu:Created>"
            "</wsse:UsernameToken>"
            "</wsse:Security></s:Header>"
        )


def raise_for_status(status: int, body: bytes, url: str, operation: str) -> None:
    if status in {401, 403}:
        raise OnvifAuthError(f"ONVIF {operation} authentication failed for {url}: HTTP {status}")
    if not 200 <= status < 300:
        raise OnvifResponseError(f"ONVIF {operation} request failed for {url}: HTTP {status}: {_preview(body)}")


def parse_soap_response(content: bytes, operation: str) -> ElementTree.Element:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise OnvifResponseError(f"ONVIF {operation} returned invalid XML: {_preview(content)}") from exc

    fault = _soap_fault(root)
    if fault:
        raise OnvifResponseError(f"ONVIF {operation} SOAP fault: {fault}")
    return root


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


def _attribute(element: ElementTree.Element, name: str) -> str | None:
    for key, value in element.attrib.items():
        if _local_name(key) == name:
            return value
    return None


def _first_text(root: ElementTree.Element, name: str) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) == name and element.text:
            text = element.text.strip()
            if text:
                return text
    return None


def _soap_fault(root: ElementTree.Element) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) == "Fault":
            parts = [
                child.text.strip()
                for child in element.iter()
                if _local_name(child.tag) in {"Text", "faultstring", "Value"} and child.text and child.text.strip()
            ]
            return ": ".join(parts) or "unknown SOAP fault"
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _preview(body: bytes) -> str:
    return body[:200].decode("utf-8", errors="replace").replace("\r", " ").replace("\n", " ")
