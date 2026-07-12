from sip_indoor_station.sip.digest import (
    NonceStore,
    build_www_authenticate,
    calculate_digest_response,
    parse_digest_header,
    validate_digest_response,
)


def test_parse_authorization_digest_header() -> None:
    params = parse_digest_header(
        'Digest username="door", realm="sip.local", nonce="abc", uri="sip:x", '
        'response="deadbeef", algorithm=MD5, qop=auth, nc=00000001, cnonce="xyz"'
    )
    assert params["username"] == "door"
    assert params["realm"] == "sip.local"
    assert params["qop"] == "auth"
    assert params["algorithm"] == "MD5"


def test_generating_www_authenticate_challenge() -> None:
    challenge = build_www_authenticate("sip.local", "nonce-1")
    assert challenge == 'Digest realm="sip.local", nonce="nonce-1", algorithm=MD5, qop="auth"'


def test_generating_stale_www_authenticate_challenge() -> None:
    challenge = build_www_authenticate("sip.local", "nonce-1", stale=True)
    assert challenge == 'Digest realm="sip.local", nonce="nonce-1", algorithm=MD5, qop="auth", stale=true'


def test_validate_correct_digest_response_with_qop_auth() -> None:
    store = NonceStore()
    nonce = store.generate()
    response = calculate_digest_response(
        "REGISTER", "door", "sip.local", "secret", "sip:server", nonce, "auth", "00000001", "abc"
    )
    assert validate_digest_response(
        "REGISTER",
        {
            "username": "door",
            "realm": "sip.local",
            "nonce": nonce,
            "uri": "sip:server",
            "response": response,
            "algorithm": "MD5",
            "qop": "auth",
            "nc": "00000001",
            "cnonce": "abc",
        },
        "door",
        "secret",
        "sip.local",
        store,
    )


def test_validate_correct_digest_response_without_qop() -> None:
    store = NonceStore()
    nonce = store.generate()
    response = calculate_digest_response("REGISTER", "door", "sip.local", "secret", "sip:server", nonce)
    assert validate_digest_response(
        "REGISTER",
        {
            "username": "door",
            "realm": "sip.local",
            "nonce": nonce,
            "uri": "sip:server",
            "response": response,
        },
        "door",
        "secret",
        "sip.local",
        store,
    )


def test_reject_invalid_digest_response() -> None:
    store = NonceStore()
    nonce = store.generate()
    assert not validate_digest_response(
        "REGISTER",
        {
            "username": "door",
            "realm": "sip.local",
            "nonce": nonce,
            "uri": "sip:server",
            "response": "bad",
        },
        "door",
        "secret",
        "sip.local",
        store,
    )
