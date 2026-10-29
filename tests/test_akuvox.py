from __future__ import annotations

import asyncio
import hashlib

from aiohttp import web
from aiohttp.test_utils import TestServer

from sip_indoor_station.vendor.akuvox.call import AkuvoxCallApi
from sip_indoor_station.vendor.akuvox.client import AkuvoxApiClient
from sip_indoor_station.vendor.akuvox.door import AkuvoxDoorApi
from sip_indoor_station.vendor.akuvox.maintenance import AkuvoxMaintenanceApi
from sip_indoor_station.vendor.akuvox.models import AkuvoxApiClientConfig
from sip_indoor_station.vendor.akuvox.snapshot import AkuvoxSnapshotProvider
from sip_indoor_station.vendor.onvif.snapshot import OnvifSnapshotResponse


def md5(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()


def digest_ok(request: web.Request, realm: str, user: str, password: str) -> bool:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Digest "):
        return False
    fields = {}
    for item in header[7:].split(","):
        key, _, value = item.strip().partition("=")
        fields[key] = value.strip('"')
    ha1 = md5(f"{user}:{realm}:{password}")
    ha2 = md5(f"GET:{fields['uri']}")
    expected = md5(f"{ha1}:{fields['nonce']}:{fields['nc']}:{fields['cnonce']}:{fields['qop']}:{ha2}")
    return fields["username"] == user and fields["response"] == expected


def challenge(realm: str) -> web.Response:
    return web.Response(
        status=401,
        headers={"WWW-Authenticate": f'Digest realm="{realm}", nonce="abc123", qop="auth", algorithm=MD5'},
    )


def make_client(server: TestServer, username: str | None = None, password: str | None = None) -> AkuvoxApiClient:
    return AkuvoxApiClient(AkuvoxApiClientConfig(host="127.0.0.1", port=server.port, username=username, password=password))


async def with_server(app: web.Application, scenario):
    server = TestServer(app)
    await server.start_server()
    try:
        return await scenario(server)
    finally:
        await server.close()


def open_door_app(seen: list[dict[str, str]]) -> web.Application:
    async def open_door(request: web.Request) -> web.Response:
        seen.append(dict(request.query))
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/fcgi/OpenDoor", open_door)
    return app


def test_open_door_maps_relays_to_door_nums() -> None:
    seen: list[dict[str, str]] = []

    async def scenario(server: TestServer) -> None:
        door = AkuvoxDoorApi(make_client(server), door_nums=["1", "2", "SA"])
        assert door.relays_count == 3
        assert await door.open_door(1) is True
        assert await door.open_door(3) is True

    asyncio.run(with_server(open_door_app(seen), scenario))
    assert seen == [
        {"action": "OpenDoor", "DoorNum": "1"},
        {"action": "OpenDoor", "DoorNum": "SA"},
    ]


def test_open_door_fails_on_http_error() -> None:
    async def scenario(server: TestServer) -> None:
        assert await AkuvoxDoorApi(make_client(server)).open_door(1) is False

    asyncio.run(with_server(web.Application(), scenario))


def test_open_door_rejects_invalid_relay_and_door_nums() -> None:
    door = AkuvoxDoorApi(AkuvoxApiClient(AkuvoxApiClientConfig(host="127.0.0.1")), door_nums=["1", "SA"])
    for relay in (0, 3):
        try:
            asyncio.run(door.open_door(relay))
        except ValueError:
            continue
        raise AssertionError("expected ValueError")
    for bad in (["1&x=2"], [""], []):
        try:
            AkuvoxDoorApi(AkuvoxApiClient(AkuvoxApiClientConfig(host="127.0.0.1")), door_nums=bad or [" "])
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


def test_digest_auth_success_and_wrong_password() -> None:
    async def info(request: web.Request) -> web.Response:
        if not digest_ok(request, "HTTP API", "api", "secret"):
            return challenge("HTTP API")
        return web.json_response({"retcode": 0, "data": {}})

    app = web.Application()
    app.router.add_get("/api/system/info", info)

    async def scenario(server: TestServer) -> None:
        assert await AkuvoxMaintenanceApi(make_client(server, "api", "secret")).health_check() is True
        assert await AkuvoxMaintenanceApi(make_client(server, "api", "wrong")).health_check() is False
        assert await AkuvoxMaintenanceApi(make_client(server)).health_check() is False

    asyncio.run(with_server(app, scenario))


def test_digest_auth_redoes_handshake_after_redirect() -> None:
    seen: list[str] = []

    async def start(request: web.Request) -> web.Response:
        if not request.headers.get("Authorization"):
            return challenge("HTTP API")
        raise web.HTTPPermanentRedirect("/api/final")

    async def final(request: web.Request) -> web.Response:
        if not digest_ok(request, "HTTP API", "api", "secret"):
            return challenge("HTTP API")
        seen.append(request.query_string)
        return web.json_response({"retcode": 0})

    app = web.Application()
    app.router.add_get("/api/start", start)
    app.router.add_get("/api/final", final)

    async def scenario(server: TestServer) -> None:
        response = await make_client(server, "api", "secret").get("/api/start", params={"x": "1"})
        assert response.status == 200

    asyncio.run(with_server(app, scenario))
    assert seen == ["x=1"]


def test_open_door_uses_api_credentials_with_digest() -> None:
    seen: list[dict[str, str]] = []

    async def open_door(request: web.Request) -> web.Response:
        if not digest_ok(request, "OPEN DOOR", "api", "secret"):
            return challenge("OPEN DOOR")
        seen.append(dict(request.query))
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/fcgi/OpenDoor", open_door)

    async def scenario(server: TestServer) -> None:
        door = AkuvoxDoorApi(make_client(server, "api", "secret"), door_nums=["1", "SB"])
        assert await door.open_door(2) is True
        assert await AkuvoxDoorApi(make_client(server, "api", "wrong")).open_door(1) is False

    asyncio.run(with_server(app, scenario))
    assert seen == [{"action": "OpenDoor", "DoorNum": "SB"}]


def test_call_status_and_hangup() -> None:
    hangups: list[bool] = []

    async def status(_request: web.Request) -> web.Response:
        return web.json_response({"retcode": 0, "data": {"Status": "READY"}})

    async def hangup(_request: web.Request) -> web.Response:
        hangups.append(True)
        return web.json_response({"retcode": 0})

    app = web.Application()
    app.router.add_get("/api/call/status", status)
    app.router.add_get("/api/call/hangup", hangup)

    async def scenario(server: TestServer) -> None:
        call = AkuvoxCallApi(make_client(server))
        assert await call.get_call_status() == "ready"
        assert await call.hangup_call() is True

    asyncio.run(with_server(app, scenario))
    assert hangups == [True]


def test_snapshot_provider_prefers_https_and_uses_onvif_credentials() -> None:
    fetched: list[str] = []

    async def fetcher(uri: str) -> OnvifSnapshotResponse:
        fetched.append(uri)
        return OnvifSnapshotResponse(200, b"jpeg", "image/jpeg")

    client = AkuvoxApiClient(AkuvoxApiClientConfig(host="h", username="api", password="p"))
    provider = AkuvoxSnapshotProvider(client, username="view", password="v")
    assert provider.config.username == "view"
    assert provider.config.password == "v"
    default = AkuvoxSnapshotProvider(client)
    assert default.config.username == "api"

    async def run() -> None:
        # fetch_snapshot is the overridden method; exercise it via a patched parent call
        from sip_indoor_station.vendor.onvif.snapshot import OnvifSnapshotProvider

        original = OnvifSnapshotProvider.fetch_snapshot

        async def fake(self, uri: str) -> OnvifSnapshotResponse:
            return await fetcher(uri)

        OnvifSnapshotProvider.fetch_snapshot = fake
        try:
            response = await provider.fetch_snapshot("http://h:8080/picture.jpg")
        finally:
            OnvifSnapshotProvider.fetch_snapshot = original
        assert response.content == b"jpeg"

    asyncio.run(run())
    assert fetched == ["https://h:8080/picture.jpg"]
