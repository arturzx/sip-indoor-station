from __future__ import annotations

import asyncio
import json

from sip_indoor_station.api.state_api import StateApi
from sip_indoor_station import __version__
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

    async def open_door(self) -> bool:
        self.calls.append("open_door")
        return True

    async def reboot(self) -> bool:
        self.calls.append("reboot")
        return True


def test_state_api_tracks_events_and_broadcasts_state() -> None:
    async def run() -> None:
        event_bus = EventBus()
        api, broadcasts = state_api_with_broadcasts(event_bus, FakeController())
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
        api, broadcasts = state_api_with_broadcasts(event_bus, controller)
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
        api, broadcasts = state_api_with_broadcasts(event_bus, controller)
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
        api, _broadcasts = state_api_with_broadcasts(event_bus, controller)
        await event_bus.publish(AppEvent("call_answered", call_id="call-1"))
        response = await api.hangup(None)  # type: ignore[arg-type]
        assert response.status == 200
        assert controller.calls == ["hangup"]

    asyncio.run(run())


def test_open_door_and_reboot_call_controller_without_call_state_requirement() -> None:
    async def run() -> None:
        controller = FakeController()
        api, _broadcasts = state_api_with_broadcasts(EventBus(), controller)
        assert (await api.open_door(None)).status == 200  # type: ignore[arg-type]
        assert (await api.reboot(None)).status == 200  # type: ignore[arg-type]
        assert controller.calls == ["open_door", "reboot"]

    asyncio.run(run())


def test_get_state_returns_json_snapshot() -> None:
    async def run() -> None:
        api, _broadcasts = state_api_with_broadcasts(EventBus(), FakeController())
        api.state.registered = True
        response = await api.get_state(None)  # type: ignore[arg-type]
        payload = json.loads(response.text)
        assert payload["version"] == __version__
        assert payload["registered"] is True
        assert payload["ringing"] is False
        assert payload["in_call"] is False

    asyncio.run(run())


def state_api_with_broadcasts(event_bus: EventBus, controller: FakeController) -> tuple[StateApi, list[dict]]:
    api = StateApi(event_bus, controller)
    broadcasts: list[dict] = []

    async def collect(payload: dict) -> None:
        broadcasts.append(payload)

    api.broadcast = collect  # type: ignore[method-assign]
    return api, broadcasts
