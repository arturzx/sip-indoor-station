from __future__ import annotations

import asyncio

from sip_indoor_station.api.state_api import StateApi
from sip_indoor_station.app.config import Config
from sip_indoor_station.app.events import AppEvent, EventBus
from sip_indoor_station.vendor.hikvision.call import HikvisionCallApi
from sip_indoor_station.vendor.hikvision.client import HikvisionIsapiClient
from sip_indoor_station.vendor.hikvision.door import HikvisionDoorApi
from sip_indoor_station.vendor.hikvision.errors import IsapiAuthError, IsapiConnectionError, IsapiResponseError
from sip_indoor_station.vendor.hikvision.maintenance import HikvisionMaintenanceApi
from sip_indoor_station.vendor.hikvision.models import IsapiBinaryResponse, IsapiClientConfig, IsapiResponse
from sip_indoor_station.vendor.hikvision.snapshot import HikvisionSnapshotProvider
from sip_indoor_station.vendor.dahua.client import DahuaApiClient
from sip_indoor_station.vendor.dahua.door import DahuaDoorApi
from sip_indoor_station.vendor.dahua.models import DahuaSnapshotResponse, DahuaApiClientConfig
from sip_indoor_station.vendor.dahua.snapshot import DahuaSnapshotProvider
from sip_indoor_station.sip.server import SipServer


class FakeDoorClient:
    def __init__(self, responses: list[IsapiResponse] | None = None) -> None:
        self.responses = responses or [IsapiResponse(200, "OK")]
        self.requests: list[tuple[str, str, object | None]] = []

    async def get(self, path: str, *, expect_json: bool = False) -> IsapiResponse:
        self.requests.append(("GET", path, None))
        return self.responses.pop(0)

    async def put(
        self,
        path: str,
        *,
        xml: str | None = None,
        json_payload: dict | None = None,
        expect_json: bool = False,
    ) -> IsapiResponse:
        self.requests.append(("PUT", path, xml if xml is not None else json_payload))
        return self.responses.pop(0)


class FakeSnapshotClient:
    def __init__(self) -> None:
        self.requests: list[str] = []

    async def get_bytes(self, path: str) -> IsapiBinaryResponse:
        self.requests.append(path)
        return IsapiBinaryResponse(200, b"snapshot", "image/jpeg")


class FakeDahuaClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, object] | None]] = []
        self.get_snapshot_calls: list[int] = []

    async def get(self, path: str, *, params: dict[str, object] | None = None, expect_json: bool = False) -> object:
        self.requests.append((path, params))
        return IsapiResponse(200, "OK")

    async def get_snapshot(self, channel: int = 1) -> DahuaSnapshotResponse:
        self.get_snapshot_calls.append(channel)
        return DahuaSnapshotResponse(200, b"snapshot")


def test_isapi_client_builds_http_base_url() -> None:
    client = HikvisionIsapiClient(IsapiClientConfig(host="192.168.8.163", port=80))
    assert client.base_url == "http://192.168.8.163:80"
    assert client.url("/ISAPI/System/deviceInfo") == "http://192.168.8.163:80/ISAPI/System/deviceInfo"


def test_isapi_client_builds_https_base_url() -> None:
    client = HikvisionIsapiClient(IsapiClientConfig(host="door.local", port=443, use_https=True))
    assert client.base_url == "https://door.local:443"


def test_open_door_sends_expected_put_and_xml_body() -> None:
    async def run() -> None:
        client = FakeDoorClient()
        door = HikvisionDoorApi(client)  # type: ignore[arg-type]
        assert await door.open_door() is True
        method, path, body = client.requests[-1]
        assert method == "PUT"
        assert path == "/ISAPI/AccessControl/RemoteControl/door/1"
        assert "<RemoteControlDoor>" in str(body)
        assert "<cmd>open</cmd>" in str(body)

    asyncio.run(run())


def test_open_door_uses_relay_index() -> None:
    async def run() -> None:
        client = FakeDoorClient()
        door = HikvisionDoorApi(client, relays_count=4)  # type: ignore[arg-type]
        assert await door.open_door(relay=3) is True
        assert client.requests[-1][1] == "/ISAPI/AccessControl/RemoteControl/door/3"

    asyncio.run(run())


def test_snapshot_provider_reads_hikvision_picture_endpoint() -> None:
    async def run() -> None:
        client = FakeSnapshotClient()
        provider = HikvisionSnapshotProvider(client)  # type: ignore[arg-type]

        snapshot = await provider.capture_snapshot()

        assert snapshot is not None
        assert snapshot.content == b"snapshot"
        assert snapshot.content_type == "image/jpeg"
        assert client.requests == ["/ISAPI/Streaming/channels/101/picture"]

    asyncio.run(run())


def test_dahua_door_api_uses_channel_relay() -> None:
    async def run() -> None:
        client = FakeDahuaClient()
        door = DahuaDoorApi(client, relays_count=2)  # type: ignore[arg-type]
        assert await door.open_door(relay=2) is True
        assert client.requests[-1] == ("/cgi-bin/accessControl.cgi", {"action": "openDoor", "channel": 2})

    asyncio.run(run())


def test_dahua_snapshot_provider_reads_dahua_snapshot_endpoint() -> None:
    async def run() -> None:
        client = FakeDahuaClient()
        provider = DahuaSnapshotProvider(client)  # type: ignore[arg-type]
        snapshot = await provider.capture_snapshot()
        assert snapshot is not None
        assert snapshot.content == b"snapshot"
        assert snapshot.content_type == "image/jpeg"
        assert client.get_snapshot_calls == [1]

    asyncio.run(run())


def test_dahua_api_builds_http_base_url() -> None:
    client = DahuaApiClient(DahuaApiClientConfig(host="dahua.local", port=80))
    assert client.base_url == "http://dahua.local:80"



def test_get_call_status_parses_idle_ring_and_on_call() -> None:
    assert HikvisionCallApi.normalize_call_status({"CallStatus": {"status": "idle"}}) == "idle"
    assert HikvisionCallApi.normalize_call_status({"CallStatus": {"callStatus": "ring"}}) == "ring"
    assert HikvisionCallApi.normalize_call_status({"status": "onCall"}) == "onCall"


def test_get_call_status_returns_unknown_on_unexpected_response() -> None:
    assert HikvisionCallApi.normalize_call_status({"unexpected": "value"}) == "unknown"
    assert HikvisionCallApi.normalize_call_status(None) == "unknown"


def test_reject_call_sends_cmd_type_reject() -> None:
    async def run() -> None:
        client = FakeDoorClient()
        call = HikvisionCallApi(client)  # type: ignore[arg-type]
        assert await call.reject_call() is True
        assert client.requests[-1] == (
            "PUT",
            "/ISAPI/VideoIntercom/callSignal?format=json",
            {"CallSignal": {"cmdType": "reject"}},
        )

    asyncio.run(run())


def test_hangup_call_sends_cmd_type_hangup() -> None:
    async def run() -> None:
        client = FakeDoorClient()
        call = HikvisionCallApi(client)  # type: ignore[arg-type]
        assert await call.hangup_call() is True
        assert client.requests[-1] == (
            "PUT",
            "/ISAPI/VideoIntercom/callSignal?format=json",
            {"CallSignal": {"cmdType": "hangUp"}},
        )

    asyncio.run(run())


def test_reboot_sends_system_reboot_put() -> None:
    async def run() -> None:
        client = FakeDoorClient()
        maintenance = HikvisionMaintenanceApi(client)  # type: ignore[arg-type]
        assert await maintenance.reboot() is True
        assert client.requests[-1] == ("PUT", "/ISAPI/System/reboot", None)

    asyncio.run(run())


def test_http_401_raises_isapi_auth_error() -> None:
    try:
        HikvisionIsapiClient.raise_for_status(401, "Unauthorized", "/path")
    except IsapiAuthError:
        return
    raise AssertionError("IsapiAuthError was not raised")


def test_non_2xx_response_raises_isapi_response_error() -> None:
    try:
        HikvisionIsapiClient.raise_for_status(500, "failed", "/path")
    except IsapiResponseError:
        return
    raise AssertionError("IsapiResponseError was not raised")


def test_invalid_json_response_raises_isapi_response_error() -> None:
    try:
        HikvisionIsapiClient.parse_json("not-json", "/path")
    except IsapiResponseError:
        return
    raise AssertionError("IsapiResponseError was not raised")


def test_timeout_raises_isapi_connection_error() -> None:
    class TimeoutClient(HikvisionIsapiClient):
        async def request(self, *args, **kwargs) -> IsapiResponse:  # type: ignore[no-untyped-def]
            raise IsapiConnectionError("ISAPI request timed out: /path")

    async def run() -> None:
        client = TimeoutClient(IsapiClientConfig(host="192.168.8.163", username="admin", password="secret"))
        try:
            await client.get("/path")
        except IsapiConnectionError as exc:
            assert "timed out" in str(exc)
            return
        raise AssertionError("IsapiConnectionError was not raised")

    asyncio.run(run())


def test_sip_server_open_door_rejected_when_isapi_disabled() -> None:
    async def run() -> None:
        events: list[AppEvent] = []
        event_bus = EventBus()
        event_bus.subscribe(lambda event: collect_event(events, event))
        server = SipServer(Config(api_enabled=False), event_bus=event_bus)
        assert await server.open_door() is False
        assert events[-1].name == "command_rejected"
        assert events[-1].data == {"command": "open_door", "reason": "api_disabled"}

    asyncio.run(run())


def test_api_open_door_broadcasts_success_event_when_isapi_enabled() -> None:
    async def run() -> None:
        event_bus = EventBus()
        server = SipServer(Config(api_enabled=True), event_bus=event_bus, door_opener=SuccessfulDoorOpener())
        api, broadcasts = state_api_with_broadcasts(event_bus, server)
        response = await api.open_door(None)  # type: ignore[arg-type]
        assert response.status == 200
        payload = broadcasts[-1]
        assert payload["event"] == "door_open_command_sent"
        assert payload["data"] == {"source": "api"}

    asyncio.run(run())


def test_api_open_door_rejected_when_isapi_disabled() -> None:
    async def run() -> None:
        event_bus = EventBus()
        server = SipServer(Config(api_enabled=False), event_bus=event_bus)
        api, broadcasts = state_api_with_broadcasts(event_bus, server)
        response = await api.open_door(None)  # type: ignore[arg-type]
        assert response.status == 409
        payload = broadcasts[-1]
        assert payload["event"] == "command_rejected"
        assert payload["command"] == "open_door"
        assert payload["reason"] == "api_disabled"

    asyncio.run(run())


def test_api_open_door_broadcasts_failure_event_when_isapi_fails() -> None:
    async def run() -> None:
        event_bus = EventBus()
        server = SipServer(Config(api_enabled=True), event_bus=event_bus, door_opener=FailingDoorOpener())
        api, broadcasts = state_api_with_broadcasts(event_bus, server)
        response = await api.open_door(None)  # type: ignore[arg-type]
        assert response.status == 409
        payload = broadcasts[-1]
        assert payload["event"] == "open_door_failed"
        assert payload["data"]["source"] == "api"
        assert payload["data"]["reason"]

    asyncio.run(run())


def test_sip_server_reboot_rejected_when_isapi_disabled() -> None:
    async def run() -> None:
        events: list[AppEvent] = []
        event_bus = EventBus()
        event_bus.subscribe(lambda event: collect_event(events, event))
        server = SipServer(Config(api_enabled=False), event_bus=event_bus)
        assert await server.reboot() is False
        assert events[-1].name == "command_rejected"
        assert events[-1].data == {"command": "reboot", "reason": "api_disabled"}

    asyncio.run(run())


def test_api_reboot_broadcasts_success_event_when_isapi_enabled() -> None:
    async def run() -> None:
        event_bus = EventBus()
        server = SipServer(Config(api_enabled=True), event_bus=event_bus, maintenance=SuccessfulMaintenance())
        api, broadcasts = state_api_with_broadcasts(event_bus, server)
        response = await api.reboot(None)  # type: ignore[arg-type]
        assert response.status == 200
        payload = broadcasts[-1]
        assert payload["event"] == "reboot_command_sent"
        assert payload["data"] == {"source": "api"}

    asyncio.run(run())


def test_api_reboot_broadcasts_failure_event_when_isapi_fails() -> None:
    async def run() -> None:
        event_bus = EventBus()
        server = SipServer(Config(api_enabled=True), event_bus=event_bus, maintenance=FailingMaintenance())
        api, broadcasts = state_api_with_broadcasts(event_bus, server)
        response = await api.reboot(None)  # type: ignore[arg-type]
        assert response.status == 409
        payload = broadcasts[-1]
        assert payload["event"] == "reboot_failed"
        assert payload["data"]["source"] == "api"
        assert payload["data"]["reason"]

    asyncio.run(run())


class SuccessfulDoorOpener:
    async def open_door(self, relay: int = 1) -> bool:
        return True


class FailingDoorOpener:
    async def open_door(self, relay: int = 1) -> bool:
        return False


class SuccessfulMaintenance:
    async def reboot(self) -> bool:
        return True


class FailingMaintenance:
    async def reboot(self) -> bool:
        return False


async def collect_event(events: list[AppEvent], event: AppEvent) -> None:
    events.append(event)


def state_api_with_broadcasts(event_bus: EventBus, server: SipServer) -> tuple[StateApi, list[dict]]:
    api = StateApi(event_bus, server, Config())
    broadcasts: list[dict] = []

    async def collect(payload: dict) -> None:
        broadcasts.append(payload)

    api.broadcast = collect  # type: ignore[method-assign]
    return api, broadcasts
