from __future__ import annotations

import asyncio
import json

from sip_indoor_station.api.state_api import StateApi
from sip_indoor_station import __version__
from sip_indoor_station.app.config import Config
from sip_indoor_station.app.events import AppEvent, EventBus


class FakeController:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def answer_current_call(self) -> bool:
        self.calls.append("answer")
        return True

    async def reject_current_call(self) -> bool:
        self.calls.append("reject")
        return True

    async def hangup_current_call(self) -> bool:
        self.calls.append("hangup")
        return True

    async def open_door(self, relay: int = 1) -> bool:
        self.calls.append(f"open_door:{relay}")
        return True

    async def reboot(self) -> bool:
        self.calls.append("reboot")
        return True


class FakeRequest:
    def __init__(self, relay: str | None = None, body: str | None = None) -> None:
        self._body = body
        self.query = {"relay": relay} if relay is not None else {}

    async def text(self) -> str:
        return self._body or ""


def test_get_config_returns_api_runtime_configuration() -> None:
    async def run() -> None:
        controller = FakeController()
        event_bus = EventBus()
        api = StateApi(
            event_bus,
            controller,
            Config(
                api_enabled=True,
                api_host="192.168.0.12",
                api_port=8080,
                api_use_https=True,
                api_timeout_seconds=7.5,
                api_verify_ssl=True,
                door_station_vendor="dahua",
                relays_count=2,
            ),
            None,
        )
        response = await api.get_config(None)  # type: ignore[arg-type]
        payload = json.loads(response.text)
        assert payload == {
            "api_enabled": True,
            "door_station_vendor": "dahua",
            "relays_count": 2,
        }

    asyncio.run(run())


def test_state_api_tracks_events_and_broadcasts_state() -> None:
    async def run() -> None:
        event_bus = EventBus()
        api, broadcasts = state_api_with_broadcasts(event_bus, FakeController(), Config())
        await event_bus.publish(AppEvent("registration_success", data={"username": "door", "source": "192.168.0.10:5060"}))
        await event_bus.publish(AppEvent("incoming_call", call_id="call-1", data={"remote_ip": "192.168.0.10"}))

        assert api.state.registered is True
        assert api.state.registration_user == "door"
        assert api.state.call_state == "ringing"
        assert broadcasts[-2]["type"] == "state"
        assert broadcasts[-2]["state"]["ringing"] is True
        assert broadcasts[-1]["type"] == "event"
        assert broadcasts[-1]["event"] == "incoming_call"

    asyncio.run(run())


def test_answer_command_calls_controller_when_ringing() -> None:
    async def run() -> None:
        controller = FakeController()
        event_bus = EventBus()
        api, broadcasts = state_api_with_broadcasts(event_bus, controller, Config())
        await event_bus.publish(AppEvent("incoming_call", call_id="call-1"))
        response = await api.answer(None)  # type: ignore[arg-type]
        assert response.status == 200
        assert controller.calls == ["answer"]
        assert broadcasts[-1]["event"] == "answer_requested"

    asyncio.run(run())


def test_answer_command_rejected_when_not_ringing() -> None:
    async def run() -> None:
        controller = FakeController()
        event_bus = EventBus()
        api, broadcasts = state_api_with_broadcasts(event_bus, controller, Config())
        response = await api.answer(None)  # type: ignore[arg-type]
        assert response.status == 409
        assert controller.calls == []
        assert broadcasts[-1]["event"] == "command_rejected"
        assert broadcasts[-1]["reason"] == "no_ringing_call"

    asyncio.run(run())


def test_hangup_command_calls_controller_when_answered() -> None:
    async def run() -> None:
        controller = FakeController()
        event_bus = EventBus()
        api, _broadcasts = state_api_with_broadcasts(event_bus, controller, Config())
        await event_bus.publish(AppEvent("call_answered", call_id="call-1"))
        response = await api.hangup(None)  # type: ignore[arg-type]
        assert response.status == 200
        assert controller.calls == ["hangup"]

    asyncio.run(run())


def test_open_door_and_reboot_call_controller_without_call_state_requirement() -> None:
    async def run() -> None:
        controller = FakeController()
        api, _broadcasts = state_api_with_broadcasts(EventBus(), controller, Config())
        assert (await api.open_door(None)).status == 200  # type: ignore[arg-type]
        assert (await api.reboot(None)).status == 200  # type: ignore[arg-type]
        assert controller.calls == ["open_door:1", "reboot"]

    asyncio.run(run())


def test_open_door_ignores_relay_query_param() -> None:
    async def run() -> None:
        controller = FakeController()
        api, _broadcasts = state_api_with_broadcasts(EventBus(), controller, Config())
        response = await api.open_door(FakeRequest("2"))  # type: ignore[arg-type]
        assert response.status == 200
        assert controller.calls == ["open_door:1"]

    asyncio.run(run())


def test_open_door_accepts_relay_from_json_body() -> None:
    async def run() -> None:
        controller = FakeController()
        api, _ = state_api_with_broadcasts(EventBus(), controller, Config())
        response = await api.open_door(FakeRequest(body='{"relay": 3}'))  # type: ignore[arg-type]
        assert response.status == 200
        assert controller.calls == ["open_door:3"]

    asyncio.run(run())


def test_open_door_rejects_invalid_relay_json_body() -> None:
    async def run() -> None:
        controller = FakeController()
        api, _ = state_api_with_broadcasts(EventBus(), controller, Config())
        response = await api.open_door(FakeRequest(body='{"relay":"abc"}'))  # type: ignore[arg-type]
        payload = json.loads(response.text)
        assert response.status == 400
        assert payload == {"ok": False, "reason": "invalid_relay"}

    asyncio.run(run())


def test_open_door_rejects_bool_relay_json_body() -> None:
    async def run() -> None:
        controller = FakeController()
        api, _ = state_api_with_broadcasts(EventBus(), controller, Config())
        response = await api.open_door(FakeRequest(body='{"relay":true}'))  # type: ignore[arg-type]
        payload = json.loads(response.text)
        assert response.status == 400
        assert payload == {"ok": False, "reason": "invalid_relay"}

    asyncio.run(run())


def test_open_door_rejects_invalid_json_body() -> None:
    async def run() -> None:
        controller = FakeController()
        api, _ = state_api_with_broadcasts(EventBus(), controller, Config())
        response = await api.open_door(FakeRequest(body="{"))  # type: ignore[arg-type]
        payload = json.loads(response.text)
        assert response.status == 400
        assert payload == {"ok": False, "reason": "invalid_relay"}

    asyncio.run(run())


def test_get_state_returns_json_snapshot() -> None:
    async def run() -> None:
        api, _broadcasts = state_api_with_broadcasts(EventBus(), FakeController(), Config())
        api.state.registered = True
        response = await api.get_state(None)  # type: ignore[arg-type]
        payload = json.loads(response.text)
        assert payload["version"] == __version__
        assert payload["registered"] is True
        assert payload["ringing"] is False
        assert payload["in_call"] is False

    asyncio.run(run())


def state_api_with_broadcasts(
    event_bus: EventBus,
    controller: FakeController,
    config: Config,
) -> tuple[StateApi, list[dict]]:
    api = StateApi(event_bus, controller, config, None)
    broadcasts: list[dict] = []

    async def collect(payload: dict) -> None:
        broadcasts.append(payload)

    api.broadcast = collect  # type: ignore[method-assign]
    return api, broadcasts
