from __future__ import annotations

import logging

from sip_indoor_station.vendor.akuvox.client import AkuvoxApiClient
from sip_indoor_station.vendor.akuvox.errors import AkuvoxApiError

LOGGER = logging.getLogger(__name__)


class AkuvoxMaintenanceApi:
    def __init__(self, client: AkuvoxApiClient) -> None:
        self.client = client

    async def health_check(self) -> bool:
        try:
            await self.client.get("/api/system/info")
            return True
        except AkuvoxApiError as exc:
            LOGGER.debug("akuvox_health_check_failed reason=%s", exc)
            return False

    async def reboot(self) -> bool:
        try:
            await self.client.get("/api/system/reboot")
            LOGGER.info("akuvox_reboot_command_sent")
            return True
        except AkuvoxApiError as exc:
            LOGGER.warning("akuvox_reboot_failed reason=%s", exc)
            return False
