from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sip_indoor_station import __version__
from sip_indoor_station.app.events import AppEvent, utc_now


def json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def iso_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@dataclass
class AppState:
    version: str = __version__
    registered: bool = False
    registration_user: str | None = None
    registration_source: str | None = None
    call_state: str = "idle"
    call_id: str | None = None
    remote_ip: str | None = None
    selected_audio_codec: str | None = None
    selected_audio_payload_type: int | None = None
    last_event: str | None = None
    last_event_at: str | None = None

    @property
    def ringing(self) -> bool:
        return self.call_state == "ringing"

    @property
    def in_call(self) -> bool:
        return self.call_state == "answered"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ringing"] = self.ringing
        payload["in_call"] = self.in_call
        return payload

    def to_json(self) -> str:
        return json_dumps(self.to_dict())


def event_payload(event: str, call_id: str | None = None, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "event": event,
        "call_id": call_id,
        "timestamp": iso_timestamp(utc_now()),
        "data": data or {},
    }


def command_rejected_payload(command: str, reason: str) -> dict[str, Any]:
    return {
        "event": "command_rejected",
        "command": command,
        "reason": reason,
        "timestamp": iso_timestamp(utc_now()),
    }


def public_event_name(name: str) -> str:
    return {
        "registration_success": "registered",
        "registration_removed": "unregistered",
        "call_answered": "answered",
        "call_confirmed": "answered",
        "call_rejected": "rejected",
        "call_cancelled": "cancelled",
        "call_ended": "ended",
        "call_failed": "failed",
    }.get(name, name)


def apply_event_to_state(state: AppState, event: AppEvent) -> AppState:
    state.last_event = public_event_name(event.name)
    state.last_event_at = iso_timestamp(event.timestamp)
    state.call_id = event.call_id or state.call_id

    if event.name == "registration_success":
        state.registered = True
        state.registration_user = event.data.get("username")
        state.registration_source = event.data.get("source")
    elif event.name in {"registration_removed", "unregistered"}:
        state.registered = False
        state.registration_user = None
        state.registration_source = None
        state.last_event = "unregistered"
    elif event.name == "incoming_call":
        state.call_state = "ringing"
        state.call_id = event.call_id
        state.remote_ip = event.data.get("remote_ip")
        state.selected_audio_codec = event.data.get("selected_audio_codec")
        state.selected_audio_payload_type = event.data.get("selected_audio_payload_type")
    elif event.name in {"call_answered", "call_confirmed"}:
        state.call_state = "answered"
        state.call_id = event.call_id or state.call_id
    elif event.name == "call_rejected":
        state.call_state = "rejected"
        state.call_id = event.call_id or state.call_id
    elif event.name == "call_cancelled":
        state.call_state = "cancelled"
        state.call_id = event.call_id or state.call_id
    elif event.name == "call_ended":
        state.call_state = "ended"
        state.call_id = event.call_id or state.call_id
    elif event.name == "call_failed":
        state.call_state = "failed"
        state.call_id = event.call_id or state.call_id

    return state
