import asyncio
import json

from sip_indoor_station.api.state_api import StateApi
from sip_indoor_station.app.config import Config
from sip_indoor_station.app.events import EventBus
from sip_indoor_station.app.http_server import AppHttpServer


class FakeMedia:
    def __init__(self) -> None:
        self.offer: str | None = None
        self.ice: list[dict] = []
        self.callback = None
        self.closed = False

    async def prepare(self) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def handle_webrtc_offer(self, sdp: str, type_: str = "offer") -> dict:
        self.offer = sdp
        return {"type": "answer", "sdp": "server-answer"}

    async def add_ice_candidate(self, candidate: dict) -> None:
        self.ice.append(candidate)

    def set_ice_candidate_callback(self, callback) -> None:
        self.callback = callback

    async def close_peer(self) -> None:
        self.closed = True


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def send_str(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True


class FakeController:
    async def answer_current_call(self) -> bool:
        return True

    async def reject_current_call(self) -> bool:
        return True

    async def hangup_current_call(self) -> bool:
        return True

    async def open_door(self, relay: int = 1) -> bool:
        return True

    async def reboot(self) -> bool:
        return True


def make_server(config: Config, event_bus: EventBus, media_provider):
    return AppHttpServer(config, event_bus, media_provider, StateApi(event_bus, FakeController()))


def test_webrtc_signaling_rejects_offer_when_no_active_call() -> None:
    async def run() -> None:
        server = make_server(Config(), EventBus(), lambda: None)
        ws = FakeWebSocket()
        await server.handle_message(ws, json.dumps({"type": "offer", "sdp": "browser-offer"}))
        payload = json.loads(ws.sent[-1])
        assert payload["type"] == "error"
        assert "no active SIP call" in payload["message"]

    asyncio.run(run())


def test_webrtc_signaling_forwards_offer_to_active_media_bridge() -> None:
    async def run() -> None:
        media = FakeMedia()
        server = make_server(Config(), EventBus(), lambda: media)
        ws = FakeWebSocket()
        await server.handle_message(ws, json.dumps({"type": "offer", "sdp": "browser-offer"}))
        assert media.offer == "browser-offer"
        payload = json.loads(ws.sent[-1])
        assert payload == {"type": "answer", "sdp": "server-answer"}

    asyncio.run(run())


def test_webrtc_signaling_forwards_browser_ice_candidate() -> None:
    async def run() -> None:
        media = FakeMedia()
        server = make_server(Config(), EventBus(), lambda: media)
        ws = FakeWebSocket()
        candidate = {"candidate": "candidate:1", "sdpMid": "0", "sdpMLineIndex": 0}
        await server.handle_message(ws, json.dumps({"type": "ice", "candidate": candidate}))
        assert media.ice == [candidate]

    asyncio.run(run())


def test_webrtc_signaling_sends_local_ice_candidate() -> None:
    async def run() -> None:
        media = FakeMedia()
        server = make_server(Config(), EventBus(), lambda: media)
        ws = FakeWebSocket()
        await server.send_ice(ws, {"candidate": "candidate:2", "sdpMid": "0", "sdpMLineIndex": 0})
        payload = json.loads(ws.sent[-1])
        assert payload["type"] == "ice"
        assert payload["candidate"]["candidate"] == "candidate:2"

    asyncio.run(run())


def test_webrtc_signaling_builds_browser_ice_servers_from_lists() -> None:
    event_bus = EventBus()
    server = AppHttpServer(
        Config(
            webrtc_stun_servers=["stun:stun1.example.com:3478", "stun:stun2.example.com:3478"],
            webrtc_turn_servers=["turn:turn1.example.com:3478", "turn:turn2.example.com:3478"],
            webrtc_turn_username="user",
            webrtc_turn_password="pass",
        ),
        event_bus,
        lambda: None,
        StateApi(event_bus, FakeController()),
    )
    assert server.browser_ice_servers() == [
        {"urls": "stun:stun1.example.com:3478"},
        {"urls": "stun:stun2.example.com:3478"},
        {"urls": "turn:turn1.example.com:3478", "username": "user", "credential": "pass"},
        {"urls": "turn:turn2.example.com:3478", "username": "user", "credential": "pass"},
    ]


def test_webrtc_signaling_does_not_expose_configured_ice_candidates_to_browser_config() -> None:
    server = make_server(
        Config(
            webrtc_ice_candidates=["51.68.137.6:8556", "192.168.8.3"],
            webrtc_ice_udp_port=8556,
        ),
        EventBus(),
        lambda: None,
    )
    response = asyncio.run(server.client_config(None))
    payload = json.loads(response.text)
    assert payload == {
        "iceServers": [],
        "iceTransportPolicy": "all",
    }


def test_webrtc_single_peer_policy_error_message() -> None:
    async def run() -> None:
        media = FakeMedia()
        server = make_server(Config(), EventBus(), lambda: media)
        ws = FakeWebSocket()
        server.active_ws = ws
        assert server.config.webrtc_single_peer is True

    asyncio.run(run())
