from __future__ import annotations

import json
import logging
from typing import Any

from aiohttp import WSMsgType, web

from sip_indoor_station.app.events import AppEvent, EventBus
from sip_indoor_station.app.state import (
    AppState,
    apply_event_to_state,
    command_rejected_payload,
    event_payload,
    public_event_name,
)
from sip_indoor_station.calls.controller import CallController
from sip_indoor_station.calls.history import CallHistoryStore

LOGGER = logging.getLogger(__name__)


class StateApi:
    def __init__(
        self,
        event_bus: EventBus,
        call_controller: CallController,
        call_history: CallHistoryStore | None = None,
    ) -> None:
        self.event_bus = event_bus
        self.call_controller = call_controller
        self.call_history = call_history
        self.state = AppState()
        self._websockets: set[web.WebSocketResponse] = set()
        self.event_bus.subscribe(self.handle_event)

    def register_routes(self, app: web.Application) -> None:
        app.router.add_get("/api/state", self.get_state)
        app.router.add_post("/api/answer", self.answer)
        app.router.add_post("/api/reject", self.reject)
        app.router.add_post("/api/hangup", self.hangup)
        app.router.add_post("/api/open_door", self.open_door)
        app.router.add_post("/api/reboot", self.reboot)
        app.router.add_get("/api/ws", self.websocket)
        if self.call_history is not None:
            self.call_history.register_routes(app)

    async def close(self) -> None:
        for ws in tuple(self._websockets):
            await ws.close()
        self._websockets.clear()
        if self.call_history is not None:
            self.call_history.close()

    async def get_state(self, _request: web.Request) -> web.Response:
        return web.json_response(self.state.to_dict())

    async def answer(self, _request: web.Request) -> web.Response:
        return await self._run_call_command("answer", self.call_controller.answer_current_call, required_state="ringing")

    async def reject(self, _request: web.Request) -> web.Response:
        return await self._run_call_command("reject", self.call_controller.reject_current_call, required_state="ringing")

    async def hangup(self, _request: web.Request) -> web.Response:
        return await self._run_call_command("hangup", self.call_controller.hangup_current_call, required_state="answered")

    async def open_door(self, _request: web.Request | None) -> web.Response:
        relay = self._parse_relay(_request)
        if relay is None:
            await self.publish_command_rejected("open_door", "invalid_relay")
            return web.json_response({"ok": False, "reason": "invalid_relay"}, status=400)
        return await self._run_call_command("open_door", lambda: self.call_controller.open_door(relay=relay))

    async def reboot(self, _request: web.Request) -> web.Response:
        return await self._run_call_command("reboot", self.call_controller.reboot)

    @staticmethod
    def _parse_relay(request: web.Request | None) -> int | None:
        if request is None:
            return 1
        relay = request.query.get("relay", "1")
        try:
            value = int(relay)
        except ValueError:
            return None
        return value if value >= 1 else None

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        self._websockets.add(ws)
        LOGGER.info("api_state_ws_connected peers=%s", len(self._websockets))
        await self._send_ws(ws, {"type": "state", "state": self.state.to_dict()})
        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT and message.data == "ping":
                    await self._send_ws(ws, {"type": "pong"})
                elif message.type == WSMsgType.ERROR:
                    LOGGER.warning("api_state_ws_error error=%s", ws.exception())
        finally:
            self._websockets.discard(ws)
            LOGGER.info("api_state_ws_disconnected peers=%s", len(self._websockets))
        return ws

    async def handle_event(self, event: AppEvent) -> None:
        if event.name == "command_rejected":
            payload = command_rejected_payload(
                str(event.data.get("command", "unknown")),
                str(event.data.get("reason", "command_rejected")),
            )
            await self.broadcast({"type": "event", **payload})
            return

        apply_event_to_state(self.state, event)
        await self.broadcast({"type": "state", "state": self.state.to_dict()})
        await self.broadcast(
            {
                "type": "event",
                **event_payload(public_event_name(event.name), event.call_id, event.data),
            }
        )

    async def _run_call_command(
        self,
        command: str,
        handler: Any,
        required_state: str | None = None,
    ) -> web.Response:
        if required_state is not None and self.state.call_state != required_state:
            reason = "no_ringing_call" if required_state == "ringing" else "no_active_call"
            await self.publish_command_rejected(command, reason)
            return web.json_response({"ok": False, "reason": reason}, status=409)

        try:
            ok = await handler()
        except Exception as exc:
            LOGGER.warning("api_command_failed command=%s error=%s", command, exc)
            await self.publish_command_rejected(command, "command_failed")
            return web.json_response({"ok": False, "reason": "command_failed"}, status=500)

        if not ok:
            return web.json_response({"ok": False, "reason": "command_rejected"}, status=409)

        if command not in {"open_door", "reboot"}:
            await self.broadcast(
                {
                    "type": "event",
                    **event_payload(f"{command}_requested", self.state.call_id),
                }
            )
        return web.json_response({"ok": True})

    async def publish_command_rejected(self, command: str, reason: str) -> None:
        LOGGER.info("api_command_rejected command=%s reason=%s", command, reason)
        await self.event_bus.publish(AppEvent("command_rejected", data={"command": command, "reason": reason}))

    async def broadcast(self, payload: dict[str, Any]) -> None:
        for ws in tuple(self._websockets):
            await self._send_ws(ws, payload)

    async def _send_ws(self, ws: web.WebSocketResponse, payload: dict[str, Any]) -> None:
        if ws.closed:
            self._websockets.discard(ws)
            return
        await ws.send_json(payload, dumps=lambda value: json.dumps(value, separators=(",", ":"), sort_keys=True))
