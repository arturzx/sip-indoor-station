import json
import time
from pathlib import Path

from sip_indoor_station.app.config import Config, SipUser
from sip_indoor_station.registrations.registry import RegistrationRegistry
from sip_indoor_station.sip.digest import calculate_digest_response, parse_digest_header
from sip_indoor_station.sip.messages import SipRequest, parse_sip_message
from sip_indoor_station.sip.server import SipServer


def make_server(sip_registration_store_path: str | None = None) -> SipServer:
    config = Config(
        sip_realm="sip.local",
        sip_registration_store_path=sip_registration_store_path,
        sip_users={"door": SipUser(username="door", password="secret", realm="sip.local")},
    )
    return SipServer(config)


def register_message(
    auth: str | None = None,
    expires: str | None = None,
    contact: str = "<sip:door@192.168.1.20:5060>",
) -> SipRequest:
    headers = [
        "REGISTER sip:server SIP/2.0",
        "Via: SIP/2.0/UDP 192.168.1.20:5060;branch=z9hG4bK-1",
        "From: <sip:door@sip.local>;tag=abc",
        "To: <sip:door@sip.local>",
        "Call-ID: call-1",
        "CSeq: 1 REGISTER",
        f"Contact: {contact}",
        "User-Agent: DoorStation",
    ]
    if expires is not None:
        headers.append(f"Expires: {expires}")
    if auth:
        headers.append(f"Authorization: {auth}")
    headers.append("Content-Length: 0")
    msg = parse_sip_message("\r\n".join(headers) + "\r\n\r\n")
    assert isinstance(msg, SipRequest)
    return msg


def authorization(server: SipServer, qop: bool = True) -> str:
    nonce = server.nonce_store.generate()
    if qop:
        response = calculate_digest_response(
            "REGISTER", "door", "sip.local", "secret", "sip:server", nonce, "auth", "00000001", "abc"
        )
        return (
            f'Digest username="door", realm="sip.local", nonce="{nonce}", uri="sip:server", '
            f'response="{response}", algorithm=MD5, qop=auth, nc=00000001, cnonce="abc"'
        )
    response = calculate_digest_response("REGISTER", "door", "sip.local", "secret", "sip:server", nonce)
    return (
        f'Digest username="door", realm="sip.local", nonce="{nonce}", uri="sip:server", '
        f'response="{response}", algorithm=MD5'
    )


def test_register_without_authorization_gets_challenge() -> None:
    server = make_server()
    response = server.handle_register(register_message(), ("192.168.1.20", 5060))
    assert response.status_code == 401
    assert parse_digest_header(response.headers["WWW-Authenticate"])["realm"] == "sip.local"


def test_storing_successful_registration() -> None:
    server = make_server()
    response = server.handle_register(register_message(authorization(server)), ("192.168.1.20", 5060))
    assert response.status_code == 200
    assert response.headers["Contact"] == "<sip:door@192.168.1.20:5060>;expires=3600"
    assert response.headers["Expires"] == "3600"
    registration = server.registrations.get("door")
    assert registration is not None
    assert registration.contact_uri == "sip:door@192.168.1.20:5060"
    assert registration.source_ip == "192.168.1.20"
    assert registration.source_port == 5060
    assert registration.user_agent == "DoorStation"


def test_unregistering_with_expires_zero() -> None:
    server = make_server()
    server.handle_register(register_message(authorization(server)), ("192.168.1.20", 5060))
    assert server.registrations.get("door") is not None
    response = server.handle_register(register_message(authorization(server), expires="0"), ("192.168.1.20", 5060))
    assert response.status_code == 200
    assert response.headers["Contact"] == "<sip:door@192.168.1.20:5060>;expires=0"
    assert response.headers["Expires"] == "0"
    assert server.registrations.get("door") is None


def test_register_response_confirms_expires_header() -> None:
    server = make_server()
    response = server.handle_register(register_message(authorization(server), expires="120"), ("192.168.1.20", 5060))

    assert response.status_code == 200
    assert response.headers["Contact"] == "<sip:door@192.168.1.20:5060>;expires=120"
    assert response.headers["Expires"] == "120"


def test_register_response_confirms_contact_expires_parameter() -> None:
    server = make_server()
    request = register_message(
        authorization(server),
        contact="<sip:door@192.168.1.20:5060>;expires=90",
    )

    response = server.handle_register(request, ("192.168.1.20", 5060))

    assert response.status_code == 200
    assert response.headers["Contact"] == "<sip:door@192.168.1.20:5060>;expires=90"
    assert response.headers["Expires"] == "90"


def test_registration_store_restores_unexpired_registration(tmp_path: Path) -> None:
    store_path = tmp_path / "registrations.json"
    server = make_server(str(store_path))
    response = server.handle_register(register_message(authorization(server)), ("192.168.1.20", 5060))

    assert response.status_code == 200
    restored = RegistrationRegistry(storage_path=store_path)
    registration = restored.get("door")
    assert registration is not None
    assert registration.contact_uri == "sip:door@192.168.1.20:5060"
    assert registration.source_ip == "192.168.1.20"
    assert registration.source_port == 5060
    assert registration.user_agent == "DoorStation"


def test_registration_store_ignores_expired_registration(tmp_path: Path) -> None:
    store_path = tmp_path / "registrations.json"
    store_path.write_text(
        json.dumps(
            {
                "version": 1,
                "registrations": [
                    {
                        "username": "door",
                        "contact_uri": "sip:door@192.168.1.20:5060",
                        "source_ip": "192.168.1.20",
                        "source_port": 5060,
                        "expires_at": time.time() - 1,
                        "user_agent": "DoorStation",
                        "last_register_time": time.time() - 3600,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    restored = RegistrationRegistry(storage_path=store_path)

    assert restored.get("door") is None
    assert restored.active() == []


def test_registration_store_persists_unregister(tmp_path: Path) -> None:
    store_path = tmp_path / "registrations.json"
    server = make_server(str(store_path))
    server.handle_register(register_message(authorization(server)), ("192.168.1.20", 5060))

    response = server.handle_register(register_message(authorization(server), expires="0"), ("192.168.1.20", 5060))

    assert response.status_code == 200
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload == {"version": 1, "registrations": []}
