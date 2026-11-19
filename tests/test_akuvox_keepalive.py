import asyncio
import logging

from sip_indoor_station.app.config import Config, SipUser
from sip_indoor_station.sip.server import SipServer


def make_server(vendor: str = "") -> SipServer:
    config = Config(
        sip_realm="sip.local",
        door_station_vendor=vendor,
        sip_users={"door": SipUser(username="door", password="secret", realm="sip.local")},
    )
    return SipServer(config)


def handle(server: SipServer, data: bytes) -> list:
    return asyncio.run(server.handle_datagram(data, ("192.168.1.2", 5060)))


AKUVOX_KEEPALIVE = b"jaK\x00"
MALFORMED_SIP = b"NOT_A_METHOD sip:foo SIP/2.0\r\n\r\n"


def test_akuvox_keepalive_logged_at_debug_when_vendor_akuvox(caplog):
    server = make_server(vendor="akuvox")
    with caplog.at_level(logging.DEBUG, logger="sip_indoor_station.sip.server"):
        result = handle(server, AKUVOX_KEEPALIVE)
    assert result == []
    assert any("sip_keepalive_ignored" in r.message for r in caplog.records)
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_akuvox_keepalive_still_warns_when_vendor_not_set(caplog):
    server = make_server(vendor="")
    with caplog.at_level(logging.DEBUG, logger="sip_indoor_station.sip.server"):
        result = handle(server, AKUVOX_KEEPALIVE)
    assert result == []
    assert any("sip_parse_failed" in r.message and r.levelno == logging.WARNING for r in caplog.records)


def test_malformed_sip_still_warns_when_vendor_akuvox(caplog):
    server = make_server(vendor="akuvox")
    with caplog.at_level(logging.DEBUG, logger="sip_indoor_station.sip.server"):
        result = handle(server, MALFORMED_SIP)
    assert result == []
    assert any("sip_parse_failed" in r.message and r.levelno == logging.WARNING for r in caplog.records)
