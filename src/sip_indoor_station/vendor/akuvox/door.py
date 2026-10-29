from __future__ import annotations

import logging
import re

from sip_indoor_station.vendor.akuvox.client import AkuvoxApiClient
from sip_indoor_station.vendor.akuvox.errors import AkuvoxApiError

LOGGER = logging.getLogger(__name__)

DOOR_NUM_PATTERN = re.compile(r"^[A-Za-z0-9]{1,4}$")


class AkuvoxDoorApi:
    """Opens doors through /fcgi/OpenDoor.

    The device only accepts the API credentials there if the same user and password are set
    under "Open Relay Via HTTP" in its web UI (Digest realm "OPEN DOOR").
    Relay N (1-based) maps to the Nth entry of door_nums, e.g. ["1", "2", "SA"].
    """

    def __init__(self, client: AkuvoxApiClient, door_nums: list[str] | None = None) -> None:
        self.client = client
        self.door_nums = [item.strip() for item in (door_nums or ["1"]) if item.strip()]
        if not self.door_nums:
            raise ValueError("at least one door number is required")
        for door_num in self.door_nums:
            if not DOOR_NUM_PATTERN.match(door_num):
                raise ValueError(f"invalid Akuvox door number: {door_num!r}")

    @property
    def relays_count(self) -> int:
        return len(self.door_nums)

    async def open_door(self, relay: int = 1) -> bool:
        door_num = self._door_num(relay)
        try:
            response = await self.client.get(
                "/fcgi/OpenDoor",
                params={"action": "OpenDoor", "DoorNum": door_num},
                expect_retcode=False,
            )
        except AkuvoxApiError as exc:
            LOGGER.warning("akuvox_open_door_failed door_num=%s reason=%s", door_num, exc)
            return False
        LOGGER.info("akuvox_open_door_success door_num=%s status=%s", door_num, response.status)
        return True

    def _door_num(self, relay: int) -> str:
        if relay < 1:
            raise ValueError("relay must be at least 1")
        if relay > len(self.door_nums):
            raise ValueError(f"relay must be in range 1..{len(self.door_nums)}")
        return self.door_nums[relay - 1]
