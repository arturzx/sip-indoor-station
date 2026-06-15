from sip_indoor_station.app.config import Config, SipUser
from sip_indoor_station.app.events import EventBus
from sip_indoor_station.registrations.registry import RegistrationRegistry
from sip_indoor_station.sip.messages import SipRequest, parse_sip_message
from sip_indoor_station.sip.server import SipServer


class FakeMediaSession:
    def __init__(self) -> None:
        self.prepared = False
        self.started = False
        self.stopped = False
        self.offers: list[str] = []
        self.ice_candidates: list[dict] = []

    async def prepare(self) -> None:
        self.prepared = True

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def handle_webrtc_offer(self, sdp: str, type_: str = "offer") -> dict:
        self.offers.append(sdp)
        return {"type": "answer", "sdp": "answer-sdp"}

    async def add_ice_candidate(self, candidate: dict) -> None:
        self.ice_candidates.append(candidate)

    def set_ice_candidate_callback(self, callback) -> None:
        self.ice_candidate_callback = callback


class FakePortAllocator:
    def __init__(self) -> None:
        self.next_port = 40000
        self.released: list[int] = []
        self.bind_ips: list[str] = []

    def allocate(self, bind_ip: str = "0.0.0.0") -> int:
        self.bind_ips.append(bind_ip)
        port = self.next_port
        self.next_port += 1
        return port

    def release(self, port: int | None) -> None:
        if port is not None:
            self.released.append(port)


def make_server() -> SipServer:
    config = Config(
        sip_realm="sip.local",
        listen_address="192.168.1.10",
        sip_users={"door": SipUser(username="door", password="secret", realm="sip.local")},
    )
    registrations = RegistrationRegistry()
    registrations.register("door", "sip:door@192.168.1.20:5060", "192.168.1.20", 5060, "DoorStation")
    return SipServer(
        config,
        registrations=registrations,
        port_allocator=FakePortAllocator(),
        media_session_factory=lambda _session: FakeMediaSession(),
    )


def invite_with_sdp(payloads: str, rtpmap: str, video: str = "") -> SipRequest:
    sdp = (
        "v=0\r\n"
        "o=- 1 1 IN IP4 192.168.1.20\r\n"
        "s=DoorStation\r\n"
        "c=IN IP4 192.168.1.20\r\n"
        "t=0 0\r\n"
        f"m=audio 4002 RTP/AVP {payloads}\r\n"
        f"{rtpmap}"
        "a=sendrecv\r\n"
        f"{video}"
    )
    message = (
        "INVITE sip:server SIP/2.0\r\n"
        "Via: SIP/2.0/UDP 192.168.1.20:5060;branch=z9hG4bK-2\r\n"
        "From: <sip:door@sip.local>;tag=abc\r\n"
        "To: <sip:server@sip.local>\r\n"
        "Call-ID: invite-1\r\n"
        "CSeq: 2 INVITE\r\n"
        "Contact: <sip:door@192.168.1.20:5060>\r\n"
        "Content-Type: application/sdp\r\n"
        f"Content-Length: {len(sdp.encode())}\r\n"
        "\r\n"
        f"{sdp}"
    )
    parsed = parse_sip_message(message)
    assert isinstance(parsed, SipRequest)
    return parsed


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))


def test_invite_pcma_ringing_flow_does_not_send_200_ok() -> None:
    server = make_server()
    request = invite_with_sdp("0 8", "a=rtpmap:0 PCMU/8000\r\na=rtpmap:8 PCMA/8000\r\n")
    responses = server.handle_invite(request, ("192.168.1.20", 5060))
    assert [response.status_code for response in responses] == [100, 180]
    assert responses[1].headers["To"].startswith("<sip:server@sip.local>;tag=")
    assert server.calls.get("invite-1") is not None


def test_invite_falls_back_to_pcmu() -> None:
    server = make_server()
    request = invite_with_sdp("0", "a=rtpmap:0 PCMU/8000\r\n")
    responses = server.handle_invite(request, ("192.168.1.20", 5060))
    assert [response.status_code for response in responses] == [100, 180]
    session = server.calls.get("invite-1")
    assert session is not None
    assert session.codec == "PCMU"
    assert session.payload_type == 0


def test_invite_rejecting_unsupported_codecs_with_488() -> None:
    server = make_server()
    request = invite_with_sdp("96", "a=rtpmap:96 OPUS/48000\r\n")
    responses = server.handle_invite(request, ("192.168.1.20", 5060))
    assert len(responses) == 1
    assert responses[0].status_code == 488


def test_invite_rejects_unregistered_source() -> None:
    server = make_server()
    request = invite_with_sdp("8", "a=rtpmap:8 PCMA/8000\r\n")
    responses = server.handle_invite(request, ("192.168.1.99", 5060))
    assert responses[0].status_code == 403


def test_reject_current_call_sends_486_to_original_invite_source() -> None:
    async def run() -> None:
        event_bus = EventBus()
        server = make_server()
        server.event_bus = event_bus
        transport = FakeTransport()
        server.transport = transport  # type: ignore[assignment]
        request = invite_with_sdp("8", "a=rtpmap:8 PCMA/8000\r\n")
        server.handle_invite(request, ("192.168.1.20", 5060))

        await server.reject_current_call()

        assert transport.sent
        data, addr = transport.sent[-1]
        raw = data.decode()
        assert addr == ("192.168.1.20", 5060)
        assert raw.startswith("SIP/2.0 486 Busy Here")
        assert "Via: SIP/2.0/UDP 192.168.1.20:5060;branch=z9hG4bK-2" in raw
        assert "From: <sip:door@sip.local>;tag=abc" in raw
        assert "Call-ID: invite-1" in raw
        assert "CSeq: 2 INVITE" in raw
        assert "To: <sip:server@sip.local>;tag=" in raw
        assert "Content-Length: 0" in raw

    import asyncio

    asyncio.run(run())


def test_reject_uses_same_to_tag_as_ringing_response() -> None:
    async def run() -> None:
        server = make_server()
        transport = FakeTransport()
        server.transport = transport  # type: ignore[assignment]
        request = invite_with_sdp("8", "a=rtpmap:8 PCMA/8000\r\n")
        responses = server.handle_invite(request, ("192.168.1.20", 5060))
        ringing_to = responses[1].headers["To"]

        await server.reject_current_call()

        rejected = parse_sip_message(transport.sent[-1][0])
        assert rejected.headers["To"] == ringing_to

    import asyncio

    asyncio.run(run())


def test_invite_retransmission_after_reject_returns_486_not_180() -> None:
    async def run() -> None:
        server = make_server()
        transport = FakeTransport()
        server.transport = transport  # type: ignore[assignment]
        request = invite_with_sdp("8", "a=rtpmap:8 PCMA/8000\r\n")
        server.handle_invite(request, ("192.168.1.20", 5060))
        await server.reject_current_call()

        responses = server.handle_invite(request, ("192.168.1.20", 5060))

        assert [response.status_code for response in responses] == [486]
        assert responses[0].reason == "Busy Here"

    import asyncio

    asyncio.run(run())


def test_ack_after_486_does_not_mark_call_confirmed() -> None:
    async def run() -> None:
        server = make_server()
        server.transport = FakeTransport()  # type: ignore[assignment]
        request = invite_with_sdp("8", "a=rtpmap:8 PCMA/8000\r\n")
        server.handle_invite(request, ("192.168.1.20", 5060))
        await server.reject_current_call()
        ack = parse_sip_message(
            "ACK sip:server SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 192.168.1.20:5060;branch=z9hG4bK-ack\r\n"
            "From: <sip:door@sip.local>;tag=abc\r\n"
            "To: <sip:server@sip.local>;tag=local\r\n"
            "Call-ID: invite-1\r\n"
            "CSeq: 2 ACK\r\n"
            "Content-Length: 0\r\n"
            "\r\n"
        )
        assert isinstance(ack, SipRequest)
        assert server.handle_ack(ack) == []
        assert server.calls.get("invite-1") is None

    import asyncio

    asyncio.run(run())


def test_invite_retransmission_after_rejected_ack_still_returns_486() -> None:
    async def run() -> None:
        server = make_server()
        server.transport = FakeTransport()  # type: ignore[assignment]
        request = invite_with_sdp("8", "a=rtpmap:8 PCMA/8000\r\n")
        server.handle_invite(request, ("192.168.1.20", 5060))
        await server.reject_current_call()
        ack = parse_sip_message(
            "ACK sip:server SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 192.168.1.20:5060;branch=z9hG4bK-ack\r\n"
            "From: <sip:door@sip.local>;tag=abc\r\n"
            "To: <sip:server@sip.local>;tag=local\r\n"
            "Call-ID: invite-1\r\n"
            "CSeq: 2 ACK\r\n"
            "Content-Length: 0\r\n"
            "\r\n"
        )
        assert isinstance(ack, SipRequest)
        server.handle_ack(ack)

        responses = server.handle_invite(request, ("192.168.1.20", 5060))

        assert [response.status_code for response in responses] == [486]

    import asyncio

    asyncio.run(run())


def test_answer_current_call_sends_200_ok_with_sdp() -> None:
    async def run() -> None:
        server = make_server()
        transport = FakeTransport()
        server.transport = transport  # type: ignore[assignment]
        request = invite_with_sdp("0 8", "a=rtpmap:0 PCMU/8000\r\na=rtpmap:8 PCMA/8000\r\n")
        server.handle_invite(request, ("192.168.1.20", 5060))

        await server.answer_current_call()

        raw = transport.sent[-1][0].decode()
        assert raw.startswith("SIP/2.0 200 OK")
        assert "Contact: <sip:sip_indoor_station@192.168.1.10:5060>" in raw
        assert "Content-Type: application/sdp" in raw
        assert "m=audio 40000 RTP/AVP 8" in raw
        assert "a=rtpmap:8 PCMA/8000" in raw
        assert server.calls.get("invite-1").state == "answered_waiting_ack"  # type: ignore[union-attr]
        assert server.calls.get("invite-1").media_session.prepared is True  # type: ignore[union-attr]

    import asyncio

    asyncio.run(run())


def test_invite_allocates_rtp_port_on_listen_address() -> None:
    config = Config(
        sip_realm="sip.local",
        listen_address="192.168.1.10",
        sip_users={"door": SipUser(username="door", password="secret", realm="sip.local")},
    )
    registrations = RegistrationRegistry()
    registrations.register("door", "sip:door@192.168.1.20:5060", "192.168.1.20", 5060, "DoorStation")
    port_allocator = FakePortAllocator()
    server = SipServer(
        config,
        registrations=registrations,
        port_allocator=port_allocator,
        media_session_factory=lambda _session: FakeMediaSession(),
    )

    request = invite_with_sdp("0", "a=rtpmap:0 PCMU/8000\r\n")
    responses = server.handle_invite(request, ("192.168.1.20", 5060))

    assert [response.status_code for response in responses] == [100, 180]
    assert port_allocator.bind_ips == ["192.168.1.10"]


def test_answer_current_call_rejects_video_in_sdp() -> None:
    async def run() -> None:
        server = make_server()
        transport = FakeTransport()
        server.transport = transport  # type: ignore[assignment]
        request = invite_with_sdp(
            "8",
            "a=rtpmap:8 PCMA/8000\r\n",
            video="m=video 4004 RTP/AVP 96 97\r\na=rtpmap:96 H264/90000\r\na=rtpmap:97 MP4V-ES/90000\r\n",
        )
        server.handle_invite(request, ("192.168.1.20", 5060))

        await server.answer_current_call()

        raw = transport.sent[-1][0].decode()
        assert raw.startswith("SIP/2.0 200 OK")
        assert "m=audio 40000 RTP/AVP 8" in raw
        assert "m=video 0 RTP/AVP 96 97" in raw
        assert "a=rtpmap:96 H264/90000" in raw
        assert "a=rtpmap:97 MP4V-ES/90000" in raw

    import asyncio

    asyncio.run(run())


def test_sdp_answer_uses_listen_address_not_loopback() -> None:
    async def run() -> None:
        config = Config(
            sip_realm="sip.local",
            listen_address="192.168.1.10",
            sip_users={"door": SipUser(username="door", password="secret", realm="sip.local")},
        )
        registrations = RegistrationRegistry()
        registrations.register("door", "sip:door@192.168.1.20:5060", "192.168.1.20", 5060, "DoorStation")
        server = SipServer(
            config,
            registrations=registrations,
            port_allocator=FakePortAllocator(),
            media_session_factory=lambda _session: FakeMediaSession(),
        )
        transport = FakeTransport()
        server.transport = transport  # type: ignore[assignment]
        request = invite_with_sdp("0", "a=rtpmap:0 PCMU/8000\r\n")
        server.handle_invite(request, ("192.168.1.20", 5060))

        await server.answer_current_call()

        raw = transport.sent[-1][0].decode()
        assert "c=IN IP4 192.168.1.10" in raw
        assert "c=IN IP4 127.0.0.1" not in raw

    import asyncio

    asyncio.run(run())


def test_sdp_answer_can_use_advertised_address_separate_from_bind_address() -> None:
    async def run() -> None:
        config = Config(
            sip_realm="sip.local",
            listen_address="0.0.0.0",
            local_address="192.168.1.10",
            sip_users={"door": SipUser(username="door", password="secret", realm="sip.local")},
        )
        registrations = RegistrationRegistry()
        registrations.register("door", "sip:door@192.168.1.20:5060", "192.168.1.20", 5060, "DoorStation")
        server = SipServer(
            config,
            registrations=registrations,
            port_allocator=FakePortAllocator(),
            media_session_factory=lambda _session: FakeMediaSession(),
        )
        transport = FakeTransport()
        server.transport = transport  # type: ignore[assignment]
        request = invite_with_sdp("0", "a=rtpmap:0 PCMU/8000\r\n")
        server.handle_invite(request, ("192.168.1.20", 5060))

        await server.answer_current_call()

        raw = transport.sent[-1][0].decode()
        assert "c=IN IP4 192.168.1.10" in raw
        assert "c=IN IP4 0.0.0.0" not in raw

    import asyncio

    asyncio.run(run())


def test_local_address_is_first_webrtc_ice_candidate() -> None:
    config = Config(
        sip_realm="sip.local",
        listen_address="0.0.0.0",
        local_address="192.168.1.10",
        webrtc_ice_candidates=["51.68.137.6:8556"],
        sip_users={"door": SipUser(username="door", password="secret", realm="sip.local")},
    )
    server = SipServer(
        config,
        registrations=RegistrationRegistry(),
        port_allocator=FakePortAllocator(),
        media_session_factory=lambda _session: FakeMediaSession(),
    )

    assert server.advertised_address() == "192.168.1.10"
    assert server.local_ice_candidates() == ["192.168.1.10", "51.68.137.6:8556"]


def test_cancel_sets_cancelled_state_event() -> None:
    async def run() -> None:
        events = []
        event_bus = EventBus()

        async def record_event(event) -> None:
            events.append(event)

        event_bus.subscribe(record_event)
        server = make_server()
        server.event_bus = event_bus
        request = invite_with_sdp("8", "a=rtpmap:8 PCMA/8000\r\n")
        server.handle_invite(request, ("192.168.1.20", 5060))
        cancel = parse_sip_message(
            "CANCEL sip:server SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 192.168.1.20:5060;branch=z9hG4bK-cancel\r\n"
            "From: <sip:door@sip.local>;tag=abc\r\n"
            "To: <sip:server@sip.local>\r\n"
            "Call-ID: invite-1\r\n"
            "CSeq: 2 CANCEL\r\n"
            "Content-Length: 0\r\n"
            "\r\n"
        )
        assert isinstance(cancel, SipRequest)

        response = server.handle_cancel(cancel)

        assert response.status_code == 200
        assert server.calls.get("invite-1").state == "cancelled"  # type: ignore[union-attr]
        await asyncio.sleep(0.01)
        assert any(event.name == "call_cancelled" for event in events)

    import asyncio

    asyncio.run(run())


def test_hangup_current_call_sends_bye_to_station() -> None:
    async def run() -> None:
        server = make_server()
        transport = FakeTransport()
        server.transport = transport  # type: ignore[assignment]
        request = invite_with_sdp("8", "a=rtpmap:8 PCMA/8000\r\n")
        server.handle_invite(request, ("192.168.1.20", 5060))
        await server.answer_current_call()
        ack = parse_sip_message(
            "ACK sip:server SIP/2.0\r\n"
            "Via: SIP/2.0/UDP 192.168.1.20:5060;branch=z9hG4bK-ack\r\n"
            "From: <sip:door@sip.local>;tag=abc\r\n"
            "To: <sip:server@sip.local>;tag=local\r\n"
            "Call-ID: invite-1\r\n"
            "CSeq: 2 ACK\r\n"
            "Content-Length: 0\r\n"
            "\r\n"
        )
        assert isinstance(ack, SipRequest)
        server.handle_ack(ack)
        media = server.calls.get("invite-1").media_session  # type: ignore[union-attr]
        assert media.started is False

        await server.hangup_current_call()

        data, addr = transport.sent[-1]
        raw = data.decode()
        assert addr == ("192.168.1.20", 5060)
        assert raw.startswith("BYE sip:door@192.168.1.20:5060 SIP/2.0")
        assert "Via: SIP/2.0/UDP 192.168.1.10:5060;branch=z9hG4bK-" in raw
        assert "From: <sip:server@sip.local>;tag=" in raw
        assert "To: <sip:door@sip.local>;tag=abc" in raw
        assert "Call-ID: invite-1" in raw
        assert "CSeq: 3 BYE" in raw
        assert "Max-Forwards: 70" in raw
        assert "Content-Length: 0" in raw
        assert server.calls.get("invite-1") is None
        assert media.stopped is True

    import asyncio

    asyncio.run(run())


def test_hangup_current_call_before_ack_still_sends_bye() -> None:
    async def run() -> None:
        server = make_server()
        transport = FakeTransport()
        server.transport = transport  # type: ignore[assignment]
        request = invite_with_sdp("8", "a=rtpmap:8 PCMA/8000\r\n")
        server.handle_invite(request, ("192.168.1.20", 5060))
        await server.answer_current_call()

        await server.hangup_current_call()

        raw = transport.sent[-1][0].decode()
        assert raw.startswith("BYE sip:door@192.168.1.20:5060 SIP/2.0")

    import asyncio

    asyncio.run(run())
