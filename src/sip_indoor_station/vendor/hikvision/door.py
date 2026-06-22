from __future__ import annotations

import logging

from sip_indoor_station.vendor.hikvision.client import HikvisionIsapiClient
from sip_indoor_station.vendor.hikvision.errors import IsapiError
from sip_indoor_station.vendor.hikvision.models import DoorCommandResult

LOGGER = logging.getLogger(__name__)


class HikvisionDoorApi:
    def __init__(self, client: HikvisionIsapiClient, relays_count: int = 1) -> None:
        self.client = client
        self.relays_count = relays_count

    async def open_door(self, relay: int = 1) -> bool:
        path = self._relay_path(relay)
        payload = self.open_door_payload()
        try:
            response = await self.client.put(path, xml=payload)
        except IsapiError as exc:
            LOGGER.warning("isapi_open_door_failed reason=%s", exc)
            return False
        LOGGER.info("isapi_open_door_success status=%s", response.status)
        return True

    async def open_door_result(self, relay: int = 1) -> DoorCommandResult:
        path = self._relay_path(relay)
        try:
            response = await self.client.put(path, xml=self.open_door_payload())
            LOGGER.info("isapi_open_door_success status=%s", response.status)
            return DoorCommandResult(True, response.status, "door open command sent")
        except IsapiError as exc:
            LOGGER.warning("isapi_open_door_failed reason=%s", exc)
            return DoorCommandResult(False, None, str(exc))

    def _relay_path(self, relay: int) -> str:
        if relay < 1:
            raise ValueError("relay must be at least 1")
        if relay > self.relays_count:
            raise ValueError(f"relay must be in range 1..{self.relays_count}")
        return f"/ISAPI/AccessControl/RemoteControl/door/{relay}"

    @staticmethod
    def open_door_payload() -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<RemoteControlDoor>"
            "<cmd>open</cmd>"
            "</RemoteControlDoor>"
        )
