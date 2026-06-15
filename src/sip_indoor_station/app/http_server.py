from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from sip_indoor_station.api.state_api import StateApi
from sip_indoor_station.app.config import Config
from sip_indoor_station.app.events import AppEvent, EventBus
from sip_indoor_station.media.base import MediaSession
from sip_indoor_station.webrtc.messages import parse_ws_message, ws_message

LOGGER = logging.getLogger(__name__)


class AppHttpServer:
    def __init__(
        self,
        config: Config,
        event_bus: EventBus,
        media_session_provider: Callable[[], MediaSession | None],
        state_api: StateApi,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self.media_session_provider = media_session_provider
        self.state_api = state_api
        self.app = web.Application()
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.active_ws: web.WebSocketResponse | None = None
        self.static_dir = Path(__file__).resolve().parents[1] / "web" / "static"

        self.app.router.add_get("/", self.index)
        self.app.router.add_get("/client.js", self.client_js)
        self.app.router.add_get("/webrtc/config", self.client_config)
        self.app.router.add_get("/webrtc/ws", self.websocket)
        self.state_api.register_routes(self.app)
        self.event_bus.subscribe(self.handle_event)

    async def start(self) -> None:
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.config.listen_address, self.config.http_port)
        await self.site.start()
        LOGGER.info("app_http_server_started host=%s port=%s", self.config.listen_address, self.config.http_port)

    async def stop(self) -> None:
        if self.active_ws is not None:
            await self.active_ws.close()
            self.active_ws = None
        await self.state_api.close()
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None

    async def index(self, _request: web.Request) -> web.FileResponse:
        return web.FileResponse(self.static_dir / "index.html")

    async def client_js(self, _request: web.Request) -> web.FileResponse:
        return web.FileResponse(self.static_dir / "client.js")

    async def client_config(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {
                "iceServers": self.browser_ice_servers(),
                "iceTransportPolicy": self.config.webrtc_ice_transport_policy,
            }
        )

    def browser_ice_servers(self) -> list[dict[str, Any]]:
        ice_servers: list[dict[str, Any]] = []
        for stun_server in self.config.webrtc_stun_servers:
            ice_servers.append({"urls": stun_server})
        for turn_server in self.config.webrtc_turn_servers:
            server: dict[str, Any] = {"urls": turn_server}
            if self.config.webrtc_turn_username:
                server["username"] = self.config.webrtc_turn_username
            if self.config.webrtc_turn_password:
                server["credential"] = self.config.webrtc_turn_password
            ice_servers.append(server)
        return ice_servers

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if self.config.webrtc_single_peer and self.active_ws is not None and not self.active_ws.closed:
            await ws.send_str(ws_message("error", message="another WebRTC peer is already connected"))
            await ws.close()
            return ws
        self.active_ws = ws
        LOGGER.info("webrtc_peer_connected")
        media = self.media_session_provider()
        if media is not None:
            media.set_ice_candidate_callback(lambda candidate: self.send_ice(ws, candidate))
        await ws.send_str(self.state_message())
        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    await self.handle_message(ws, message.data)
                elif message.type == WSMsgType.ERROR:
                    LOGGER.warning("webrtc_ws_error error=%s", ws.exception())
        finally:
            if self.active_ws is ws:
                self.active_ws = None
            media = self.media_session_provider()
            if media is not None:
                await media.close_peer()
            LOGGER.info("webrtc_peer_disconnected")
        return ws

    async def handle_message(self, ws: web.WebSocketResponse, text: str) -> None:
        try:
            payload = parse_ws_message(text)
            message_type = payload["type"]
            media = self.media_session_provider()
            if message_type == "offer":
                if media is None:
                    await ws.send_str(ws_message("error", message="no active SIP call with prepared media"))
                    return
                answer = await media.handle_webrtc_offer(payload.get("sdp", ""), "offer")
                await ws.send_str(ws_message("answer", sdp=answer["sdp"]))
            elif message_type == "ice":
                if media is None:
                    await ws.send_str(ws_message("error", message="no active SIP call with prepared media"))
                    return
                await media.add_ice_candidate(payload.get("candidate") or {})
            elif message_type == "close":
                await ws.close()
            else:
                await ws.send_str(ws_message("error", message=f"unsupported message type: {message_type}"))
        except Exception as exc:
            LOGGER.warning("webrtc_ws_message_failed error=%s", exc)
            await ws.send_str(ws_message("error", message=str(exc)))

    async def send_ice(self, ws: web.WebSocketResponse, candidate: dict[str, Any]) -> None:
        if not ws.closed:
            await ws.send_str(ws_message("ice", candidate=candidate))

    async def handle_event(self, event: AppEvent) -> None:
        if event.name in {"call_ended", "call_rejected", "call_cancelled", "call_failed"} and self.active_ws is not None:
            await self.active_ws.send_str(self.state_message())
            await self.active_ws.close()

    def state_message(self) -> str:
        media = self.media_session_provider()
        return ws_message(
            "state",
            call_state=self.state_api.state.call_state,
            media_ready=media is not None,
            webrtc_connected=self.active_ws is not None and not self.active_ws.closed,
        )
