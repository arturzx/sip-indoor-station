from __future__ import annotations

import os
from dataclasses import dataclass, field


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_csv(name: str) -> list[str]:
    value = os.getenv(name)
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def env_int_optional(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return int(value)


@dataclass(frozen=True)
class SipUser:
    username: str
    password: str
    realm: str


@dataclass(frozen=True)
class Config:
    listen_address: str = os.getenv("LISTEN_ADDRESS", "0.0.0.0")
    local_address: str | None = os.getenv("LOCAL_ADDRESS")
    sip_port: int = int(os.getenv("SIP_PORT", "5060"))
    sip_realm: str = os.getenv("SIP_REALM", "sip.local")
    sip_nonce_ttl: int = int(os.getenv("SIP_NONCE_TTL", "300"))
    sip_registration_ttl: int = int(os.getenv("SIP_REGISTRATION_TTL", "3600"))
    sip_registration_store_path: str | None = os.getenv("SIP_REGISTRATION_STORE_PATH")
    sip_users: dict[str, SipUser] = field(default_factory=dict)
    sip_reject_response_code: int = int(os.getenv("SIP_REJECT_RESPONSE_CODE", "486"))
    sip_reject_response_reason: str = os.getenv("SIP_REJECT_RESPONSE_REASON", "Busy Here")
    rtp_port_min: int = int(os.getenv("RTP_PORT_MIN", "40000"))
    rtp_port_max: int = int(os.getenv("RTP_PORT_MAX", "40100"))
    rtp_jitter_buffer_ms: int = int(os.getenv("RTP_JITTER_BUFFER_MS", "60"))
    http_port: int = int(os.getenv("HTTP_PORT", "8080"))
    webrtc_single_peer: bool = env_bool("WEBRTC_SINGLE_PEER", True)
    webrtc_ice_candidates: list[str] = field(default_factory=lambda: env_csv("WEBRTC_ICE_CANDIDATES"))
    webrtc_ice_udp_port: int | None = field(default_factory=lambda: env_int_optional("WEBRTC_ICE_UDP_PORT"))
    webrtc_stun_servers: list[str] = field(default_factory=lambda: env_csv("WEBRTC_STUN_SERVERS"))
    webrtc_turn_servers: list[str] = field(default_factory=lambda: env_csv("WEBRTC_TURN_SERVERS"))
    webrtc_turn_username: str | None = os.getenv("WEBRTC_TURN_USERNAME")
    webrtc_turn_password: str | None = os.getenv("WEBRTC_TURN_PASSWORD")
    webrtc_ice_transport_policy: str = os.getenv("WEBRTC_ICE_TRANSPORT_POLICY", "all")
    isapi_enabled: bool = env_bool("ISAPI_ENABLED", False)
    isapi_host: str | None = os.getenv("ISAPI_HOST")
    isapi_port: int = int(os.getenv("ISAPI_PORT", "80"))
    isapi_username: str | None = os.getenv("ISAPI_USERNAME")
    isapi_password: str | None = os.getenv("ISAPI_PASSWORD")
    isapi_use_https: bool = env_bool("ISAPI_USE_HTTPS", False)
    isapi_timeout_seconds: float = float(os.getenv("ISAPI_TIMEOUT_SECONDS", "5"))
    isapi_verify_ssl: bool = env_bool("ISAPI_VERIFY_SSL", False)
    isapi_door_id: int = int(os.getenv("ISAPI_DOOR_ID", "1"))


def load_config() -> Config:
    username = os.getenv("SIP_USERNAME", "door")
    password = os.getenv("SIP_PASSWORD", "door")
    realm = os.getenv("SIP_REALM", "sip.local")
    return Config(
        sip_realm=realm,
        sip_users={username: SipUser(username=username, password=password, realm=realm)},
    )
