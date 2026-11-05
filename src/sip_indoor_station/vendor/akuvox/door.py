from __future__ import annotations

import logging

from sip_indoor_station.vendor.akuvox.client import AkuvoxApiClient
from sip_indoor_station.vendor.akuvox.errors import AkuvoxApiError

LOGGER = logging.getLogger(__name__)


class AkuvoxDoorApi:
    def __init__(self, client: AkuvoxApiClient, relays_count: int = 1) -> None:
        self.client = client
        self.relays_count = relays_count

    async def open_door(self, relay: int = 1) -> bool:
        self._validate_relay(relay)
        try:
            response = await self.client.get(
                "/fcgi/OpenDoor",
                params={"action": "OpenDoor", "DoorNum": relay},
            )
            LOGGER.info("akuvox_open_door_success status=%s", response.status)
            return True
        except AkuvoxApiError as exc:
            LOGGER.warning("akuvox_open_door_failed reason=%s", exc)
            return False

    def _validate_relay(self, relay: int) -> None:
        if relay < 1:
            raise ValueError("relay must be at least 1")
        if relay > self.relays_count:
            raise ValueError(f"relay must be in range 1..{self.relays_count}")