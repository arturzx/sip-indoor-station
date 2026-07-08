from __future__ import annotations

import hashlib
import logging

from sip_indoor_station.vendor.dnake.client import DnakeApiClient
from sip_indoor_station.vendor.dnake.errors import DnakeApiError

LOGGER = logging.getLogger(__name__)


class DnakeDoorApi:
    def __init__(self, client: DnakeApiClient, relays_count: int = 1) -> None:
        self.client = client
        self.relays_count = relays_count

    async def open_door(self, relay: int = 1) -> bool:
        self._validate_relay(relay)
        try:
            response = await self.client.get(
                "/cgi-bin/webapi.cgi",
                params={
                    "api": "unlock",
                    "index": relay - 1,
                    "username": self.client.config.username,
                    "password": self.password_md5(self.client.config.password or ""),
                },
            )
            LOGGER.info("dnake_open_door_success status=%s", response.status)
            return True
        except DnakeApiError as exc:
            LOGGER.warning("dnake_open_door_failed reason=%s", exc)
            return False

    def _validate_relay(self, relay: int) -> None:
        if relay < 1:
            raise ValueError("relay must be at least 1")
        if relay > self.relays_count:
            raise ValueError(f"relay must be in range 1..{self.relays_count}")

    @staticmethod
    def password_md5(password: str) -> str:
        return hashlib.md5(password.encode("utf-8"), usedforsecurity=False).hexdigest()
