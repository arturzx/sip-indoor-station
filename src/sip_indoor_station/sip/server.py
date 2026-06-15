from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from collections.abc import Callable
from typing import Protocol

from sip_indoor_station.app.config import Config, SipUser
from sip_indoor_station.app.events import AppEvent, EventBus
from sip_indoor_station.media.base import MediaSession
from sip_indoor_station.media.gstreamer_webrtc_bridge import GStreamerWebRtcBridge
from sip_indoor_station.media.ports import RtpPortAllocator
from sip_indoor_station.calls.registry import CallRegistry
from sip_indoor_station.calls.session import CallSession
from sip_indoor_station.registrations.registry import (
    RegistrationRegistry,
    contact_uri_from_header,
    expires_from_register,
)
from sip_indoor_station.sip.digest import (
    NonceStore,
    build_www_authenticate,
    parse_digest_header,
    validate_digest_response,
)
from sip_indoor_station.sip.headers import Headers
from sip_indoor_station.sip.messages import SipRequest, SipResponse, build_sip_message, parse_sip_message, response_from_request
from sip_indoor_station.sip.sdp import build_sdp_answer, parse_sdp, select_audio_codec

LOGGER = logging.getLogger(__name__)


class DoorOpener(Protocol):
    async def open_door(self) -> bool:
        raise NotImplementedError


class StationMaintenance(Protocol):
    async def reboot(self) -> bool:
        raise NotImplementedError


def redact_sip_text(data: bytes | str) -> str:
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    return re.sub(r'(response=)(?:"[^"]+"|[^,\s]+)', r'\1"<redacted>"', text)


class SipDatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, server: "SipServer") -> None:
        self.server = server
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        self.server.transport = self.transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        asyncio.create_task(self.server.handle_datagram(data, addr))

    def error_received(self, exc: Exception) -> None:
        LOGGER.warning("udp_error error=%s", exc)


class SipServer:
    def __init__(
        self,
        config: Config,
        registrations: RegistrationRegistry | None = None,
        calls: CallRegistry | None = None,
        nonce_store: NonceStore | None = None,
        event_bus: EventBus | None = None,
        port_allocator: RtpPortAllocator | None = None,
        media_session_factory: Callable[[CallSession], MediaSession] | None = None,
        door_opener: DoorOpener | None = None,
        maintenance: StationMaintenance | None = None,
    ) -> None:
        self.config = config
        self.registrations = registrations or RegistrationRegistry(
            config.sip_registration_ttl,
            storage_path=config.sip_registration_store_path,
        )
        self.calls = calls or CallRegistry()
        self.nonce_store = nonce_store or NonceStore(config.sip_nonce_ttl)
        self.event_bus = event_bus or EventBus()
        self.port_allocator = port_allocator or RtpPortAllocator(config.rtp_port_min, config.rtp_port_max)
        self.media_session_factory = media_session_factory
        self.door_opener = door_opener
        self.maintenance = maintenance
        self.transport: asyncio.DatagramTransport | None = None
        self._completed_invite_responses: dict[tuple[str, str, tuple[str, int]], tuple[SipResponse, float]] = {}

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(
            lambda: SipDatagramProtocol(self),
            local_addr=(self.config.listen_address, self.config.sip_port),
        )
        LOGGER.info("sip_server_started host=%s port=%s", self.config.listen_address, self.config.sip_port)

    async def handle_datagram(self, data: bytes, addr: tuple[str, int]) -> list[SipResponse]:
        LOGGER.debug("sip_in addr=%s message=%s", addr, redact_sip_text(data))
        try:
            message = parse_sip_message(data)
        except ValueError as exc:
            LOGGER.warning("sip_parse_failed addr=%s error=%s", addr, exc)
            return []
        if not isinstance(message, SipRequest):
            return []
        responses = await self.handle_request(message, addr)
        for response in responses:
            self.send_response(response, addr)
        return responses

    async def handle_request(self, request: SipRequest, addr: tuple[str, int]) -> list[SipResponse]:
        method = request.method.upper()
        if method == "REGISTER":
            return [self.handle_register(request, addr)]
        if method == "OPTIONS":
            return [response_from_request(request, 200, "OK", [("Allow", "REGISTER, OPTIONS, INVITE, ACK, BYE, CANCEL")])]
        if method == "INVITE":
            return self.handle_invite(request, addr)
        if method == "ACK":
            return self.handle_ack(request)
        if method == "BYE":
            return [self.handle_bye(request)]
        if method == "CANCEL":
            return [self.handle_cancel(request)]
        return [response_from_request(request, 405, "Method Not Allowed", [("Allow", "REGISTER, OPTIONS, INVITE, ACK, BYE, CANCEL")])]

    def handle_register(self, request: SipRequest, addr: tuple[str, int]) -> SipResponse:
        authorization = request.headers.get("Authorization")
        if not authorization:
            return self.unauthorized(request)

        params = parse_digest_header(authorization)
        user = self.config.sip_users.get(params.get("username", ""))
        if not user:
            LOGGER.info("registration_failed reason=unknown_user source=%s username=%s", addr, params.get("username"))
            return self.unauthorized(request)
        if not self._validate_digest(request.method, request.uri, params, user):
            LOGGER.info("registration_failed reason=bad_digest source=%s username=%s", addr, user.username)
            return response_from_request(request, 403, "Forbidden")

        contact_uri = contact_uri_from_header(request.headers.get("Contact"))
        if not contact_uri:
            return response_from_request(request, 400, "Bad Request")
        expires = expires_from_register(request, self.config.sip_registration_ttl)
        registration = self.registrations.register(
            username=user.username,
            contact_uri=contact_uri,
            source_ip=addr[0],
            source_port=addr[1],
            user_agent=request.headers.get("User-Agent"),
            expires=expires,
        )
        if registration:
            LOGGER.info(
                "registration_success username=%s contact=%s source=%s expires_at=%s",
                user.username,
                contact_uri,
                addr,
                registration.expires_at,
            )
            self.emit(
                "registration_success",
                data={
                    "username": user.username,
                    "contact_uri": contact_uri,
                    "source": f"{addr[0]}:{addr[1]}",
                    "user_agent": request.headers.get("User-Agent"),
                },
            )
        else:
            LOGGER.info("registration_removed username=%s source=%s", user.username, addr)
            self.emit(
                "registration_removed",
                data={"username": user.username, "source": f"{addr[0]}:{addr[1]}"},
            )
        return response_from_request(request, 200, "OK", [("Contact", request.headers["Contact"])])

    def unauthorized(self, request: SipRequest) -> SipResponse:
        nonce = self.nonce_store.generate()
        return response_from_request(
            request,
            401,
            "Unauthorized",
            [("WWW-Authenticate", build_www_authenticate(self.config.sip_realm, nonce))],
        )

    def _validate_digest(self, method: str, uri: str, params: dict[str, str], user: SipUser) -> bool:
        return validate_digest_response(
            method=method,
            params=params,
            username=user.username,
            password=user.password,
            realm=user.realm,
            nonce_store=self.nonce_store,
            expected_uri=uri,
        )

    def handle_invite(self, request: SipRequest, addr: tuple[str, int]) -> list[SipResponse]:
        call_id = request.headers.get("Call-ID", "")
        cseq = request.headers.get("CSeq", "")
        transaction_key = (call_id, cseq, addr)
        cached_response = self.completed_invite_response(transaction_key)
        if cached_response is not None:
            LOGGER.info("invite_completed_retransmission call_id=%s", call_id)
            return [cached_response]

        existing = self.calls.get(call_id)
        if existing and existing.invite_cseq == cseq and existing.invite_source == addr:
            LOGGER.info("invite_retransmission call_id=%s state=%s", call_id, existing.state)
            if existing.last_final_response is not None:
                return [existing.last_final_response]
            if existing.state == "ringing":
                return [self.response_for_call(existing, 180, "Ringing")]
            return []

        if not self.registrations.find_by_source(addr[0], addr[1]):
            return [response_from_request(request, 403, "Forbidden")]
        if (request.headers.get("Content-Type") or "").lower() != "application/sdp":
            return [response_from_request(request, 488, "Not Acceptable Here")]

        offer = parse_sdp(request.body)
        selected = select_audio_codec(offer)
        if not selected or not offer.remote_ip or not offer.audio_port:
            return [response_from_request(request, 488, "Not Acceptable Here")]

        codec, payload_type = selected
        remote_target_uri = contact_uri_from_header(request.headers.get("Contact")) or request.uri
        local_rtp_port = self.port_allocator.allocate(self.config.listen_address)
        LOGGER.info(
            "call_media_selected call_id=%s codec=%s payload_type=%s advertised_rtp=%s:%s bind_rtp=%s:%s remote_rtp=%s:%s",
            call_id,
            codec,
            payload_type,
            self.advertised_address(),
            local_rtp_port,
            self.config.listen_address,
            local_rtp_port,
            offer.remote_ip,
            offer.audio_port,
        )
        session = CallSession(
            call_id=call_id,
            invite_request=request,
            invite_source=addr,
            invite_cseq=cseq,
            remote_target_uri=remote_target_uri,
            local_to_tag=secrets.token_hex(8),
            local_rtp_port=local_rtp_port,
            remote_ip=offer.remote_ip,
            remote_port=offer.audio_port,
            codec=codec,
            payload_type=payload_type,
            video_offered=offer.video_offered,
            video_payload_types=list(offer.video_payload_types),
            video_codec_mappings=dict(offer.video_codec_mappings),
            video_fmtp=dict(offer.video_fmtp),
            local_cseq=self.next_local_cseq(cseq),
        )
        self.calls.add(session)
        self.emit(
            "incoming_call",
            call_id=call_id,
            data={
                "remote_ip": offer.remote_ip,
                "remote_port": offer.audio_port,
                "selected_audio_codec": codec,
                "selected_audio_payload_type": payload_type,
            },
        )
        return [
            response_from_request(request, 100, "Trying"),
            self.response_for_call(session, 180, "Ringing"),
        ]

    def handle_ack(self, request: SipRequest) -> list[SipResponse]:
        session = self.calls.get(request.headers.get("Call-ID"))
        if session and session.state == "answered_waiting_ack":
            session.transition("confirmed")
            self.emit("call_confirmed", call_id=session.call_id)
        elif session and session.state == "rejected":
            if session.last_final_response is not None:
                self.remember_completed_invite(session, session.last_final_response)
            session.transition("ended")
            self.calls.remove(session.call_id)
            LOGGER.info("rejected_call_ack call_id=%s", session.call_id)
        return []

    def handle_bye(self, request: SipRequest) -> SipResponse:
        session = self.calls.remove(request.headers.get("Call-ID"))
        if session:
            session.transition("ended")
            self.stop_media_nowait(session)
            self.release_call_media(session)
            self.emit("call_ended", call_id=session.call_id)
        return response_from_request(request, 200, "OK")

    def handle_cancel(self, request: SipRequest) -> SipResponse:
        session = self.calls.get(request.headers.get("Call-ID"))
        if session and session.state == "ringing":
            session.transition("cancelled")
            self.stop_media_nowait(session)
            self.release_call_media(session)
            self.emit("call_cancelled", call_id=session.call_id)
        return response_from_request(request, 200, "OK")

    async def answer_current_call(self) -> bool:
        session = self.calls.current()
        if not session or session.state != "ringing":
            LOGGER.info("answer_current_call_ignored reason=no_ringing_call")
            await self.publish_command_rejected("answer", "no_ringing_call")
            return False
        response = self.response_for_call(
            session,
            200,
            "OK",
            body=await self.prepare_media_and_build_sdp_answer(session),
            content_type="application/sdp",
            extra_headers=[("Contact", self.server_contact_header())],
        )
        session.last_final_response = response
        self.remember_completed_invite(session, response)
        self.send_response(response, session.invite_source)
        session.transition("answered_waiting_ack")
        await self.event_bus.publish(AppEvent("call_answered", call_id=session.call_id))
        return True

    async def reject_current_call(self) -> bool:
        session = self.calls.current()
        if not session or session.state != "ringing":
            LOGGER.info("reject_current_call_ignored reason=no_ringing_call")
            await self.publish_command_rejected("reject", "no_ringing_call")
            return False
        response = self.response_for_call(
            session,
            self.config.sip_reject_response_code,
            self.config.sip_reject_response_reason,
        )
        session.last_final_response = response
        self.send_response(response, session.invite_source)
        session.transition("rejected")
        if session.media_session is not None:
            await session.media_session.stop()
        self.release_call_media(session)
        await self.event_bus.publish(AppEvent("call_rejected", call_id=session.call_id))
        return True

    async def hangup_current_call(self) -> bool:
        session = self.calls.current()
        if not session or session.state not in {"answered_waiting_ack", "confirmed"}:
            LOGGER.info("hangup_current_call_ignored reason=no_active_call")
            await self.publish_command_rejected("hangup", "no_active_call")
            return False
        bye = self.bye_for_call(session)
        self.send_request(bye, session.invite_source)
        session.transition("terminating")
        self.calls.remove(session.call_id)
        if session.media_session is not None:
            await session.media_session.stop()
        self.release_call_media(session)
        await self.event_bus.publish(AppEvent("call_ended", call_id=session.call_id))
        return True

    async def open_door(self) -> bool:
        if self.door_opener is None:
            reason = "isapi_disabled" if not self.config.isapi_enabled else "isapi_not_configured"
            LOGGER.info("open_door_ignored reason=%s", reason)
            await self.publish_command_rejected("open_door", reason)
            return False
        try:
            if hasattr(self.door_opener, "open_door_result"):
                result = await self.door_opener.open_door_result()  # type: ignore[attr-defined]
                if result.success:
                    await self.event_bus.publish(AppEvent("door_open_command_sent", data={"source": "isapi"}))
                    return True
                reason = result.message or "ISAPI open door command failed"
                await self.event_bus.publish(AppEvent("open_door_failed", data={"source": "isapi", "reason": reason}))
                return False
            if await self.door_opener.open_door():
                await self.event_bus.publish(AppEvent("door_open_command_sent", data={"source": "isapi"}))
                return True
            reason = "ISAPI open door command failed"
        except Exception as exc:
            LOGGER.warning("open_door_failed error=%s", exc)
            reason = str(exc)
        await self.event_bus.publish(AppEvent("open_door_failed", data={"source": "isapi", "reason": reason}))
        return False

    async def reboot(self) -> bool:
        if self.maintenance is None:
            reason = "isapi_disabled" if not self.config.isapi_enabled else "isapi_not_configured"
            LOGGER.info("reboot_ignored reason=%s", reason)
            await self.publish_command_rejected("reboot", reason)
            return False
        try:
            if await self.maintenance.reboot():
                await self.event_bus.publish(AppEvent("reboot_command_sent", data={"source": "isapi"}))
                return True
            reason = "ISAPI reboot command failed"
        except Exception as exc:
            LOGGER.warning("reboot_failed error=%s", exc)
            reason = str(exc)
        await self.event_bus.publish(AppEvent("reboot_failed", data={"source": "isapi", "reason": reason}))
        return False

    async def publish_command_rejected(self, command: str, reason: str) -> None:
        await self.event_bus.publish(
            AppEvent("command_rejected", data={"command": command, "reason": reason})
        )

    def emit(self, name: str, call_id: str | None = None, data: dict[str, object] | None = None) -> None:
        self.event_bus.publish_nowait(AppEvent(name=name, call_id=call_id, data=data or {}))

    def response_for_call(
        self,
        session: CallSession,
        status_code: int,
        reason: str,
        body: str = "",
        content_type: str | None = None,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> SipResponse:
        response = response_from_request(
            session.invite_request,
            status_code,
            reason,
            extra_headers=extra_headers,
            body=body,
            content_type=content_type,
        )
        to_header = response.headers.get("To") or ""
        if "tag=" not in to_header.lower():
            response.headers.set("To", f"{to_header};tag={session.local_to_tag}")
        return response

    async def prepare_media_and_build_sdp_answer(self, session: CallSession) -> str:
        if session.media_session is None:
            session.media_session = self.create_media_session(session)
            await session.media_session.prepare()
        session.sdp_answer = build_sdp_answer(
            self.advertised_address(),
            session.local_rtp_port,
            session.codec,
            session.payload_type,
            reject_video_payload_types=session.video_payload_types if session.video_offered else None,
            reject_video_codec_mappings=session.video_codec_mappings,
            reject_video_fmtp=session.video_fmtp,
        )
        return session.sdp_answer

    def create_media_session(self, session: CallSession) -> MediaSession:
        if self.media_session_factory is not None:
            return self.media_session_factory(session)
        return GStreamerWebRtcBridge(
            call_id=session.call_id,
            local_media_ip=self.advertised_address(),
            local_bind_ip=self.config.listen_address,
            local_rtp_port=session.local_rtp_port,
            remote_rtp_ip=session.remote_ip,
            remote_rtp_port=session.remote_port,
            selected_codec=session.codec,
            selected_payload_type=session.payload_type,
            jitter_buffer_ms=self.config.rtp_jitter_buffer_ms,
            single_peer=self.config.webrtc_single_peer,
            stun_servers=self.config.webrtc_stun_servers,
            turn_servers=self.config.webrtc_turn_servers,
            turn_username=self.config.webrtc_turn_username,
            turn_password=self.config.webrtc_turn_password,
            ice_transport_policy=self.config.webrtc_ice_transport_policy,
            ice_candidates=self.local_ice_candidates(),
            ice_udp_port=self.config.webrtc_ice_udp_port,
        )

    def active_media_session(self) -> MediaSession | None:
        session = self.calls.current()
        if session is None or session.state not in {"answered_waiting_ack", "confirmed"}:
            return None
        return session.media_session

    def release_call_media(self, session: CallSession) -> None:
        self.port_allocator.release(session.local_rtp_port)

    def listen_address(self) -> str:
        return self.config.listen_address

    def advertised_address(self) -> str:
        return self.config.local_address or self.config.listen_address

    def local_ice_candidates(self) -> list[str]:
        candidates = list(self.config.webrtc_ice_candidates)
        if self.config.local_address:
            return [self.config.local_address, *candidates]
        return candidates

    def start_media_nowait(self, session: CallSession) -> None:
        if session.media_session is None:
            return
        try:
            asyncio.get_running_loop().create_task(session.media_session.start())
        except RuntimeError:
            LOGGER.warning("media_start_skipped_no_event_loop call_id=%s", session.call_id)

    def stop_media_nowait(self, session: CallSession) -> None:
        if session.media_session is None:
            return
        try:
            asyncio.get_running_loop().create_task(session.media_session.stop())
        except RuntimeError:
            LOGGER.warning("media_stop_skipped_no_event_loop call_id=%s", session.call_id)

    def bye_for_call(self, session: CallSession) -> SipRequest:
        headers = Headers()
        headers.add(
            "Via",
            f"SIP/2.0/UDP {self.advertised_address()}:{self.config.sip_port};branch=z9hG4bK-{secrets.token_hex(8)}",
        )
        headers.add("Max-Forwards", "70")
        headers.add("From", self.local_dialog_header(session))
        to_header = session.invite_request.headers.get("From")
        if to_header is not None:
            headers.add("To", to_header)
        headers.add("Call-ID", session.call_id)
        headers.add("CSeq", f"{session.local_cseq} BYE")
        headers.add("Contact", self.server_contact_header())
        return SipRequest(method="BYE", uri=session.remote_target_uri, headers=headers)

    def local_dialog_header(self, session: CallSession) -> str:
        header = session.invite_request.headers.get("To") or f"<sip:sip_indoor_station@{self.advertised_address()}>"
        if "tag=" in header.lower():
            return header
        return f"{header};tag={session.local_to_tag}"

    def server_contact_header(self) -> str:
        return f"<sip:sip_indoor_station@{self.advertised_address()}:{self.config.sip_port}>"

    def next_local_cseq(self, remote_cseq: str) -> int:
        try:
            return int(remote_cseq.split()[0]) + 1
        except (IndexError, ValueError):
            return 1

    def remember_completed_invite(self, session: CallSession, response: SipResponse) -> None:
        self._completed_invite_responses[
            (session.call_id, session.invite_cseq, session.invite_source)
        ] = (response, time.time() + 32.0)

    def completed_invite_response(
        self,
        key: tuple[str, str, tuple[str, int]],
    ) -> SipResponse | None:
        now = time.time()
        expired = [
            cache_key
            for cache_key, (_, expires_at) in self._completed_invite_responses.items()
            if expires_at < now
        ]
        for cache_key in expired:
            self._completed_invite_responses.pop(cache_key, None)
        cached = self._completed_invite_responses.get(key)
        if cached is None:
            return None
        return cached[0]

    def send_response(self, response: SipResponse, addr: tuple[str, int]) -> None:
        data = build_sip_message(response)
        LOGGER.debug("sip_out addr=%s message=%s", addr, redact_sip_text(data))
        if self.transport:
            self.transport.sendto(data, addr)

    def send_request(self, request: SipRequest, addr: tuple[str, int]) -> None:
        data = build_sip_message(request)
        LOGGER.debug("sip_out addr=%s message=%s", addr, redact_sip_text(data))
        if self.transport:
            self.transport.sendto(data, addr)


async def build_server(config: Config) -> SipServer:
    server = SipServer(config)
    await server.start()
    return server
