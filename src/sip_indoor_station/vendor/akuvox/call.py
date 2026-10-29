from __future__ import annotations

import logging

from sip_indoor_station.vendor.akuvox.client import AkuvoxApiClient
from sip_indoor_station.vendor.akuvox.errors import AkuvoxApiError

LOGGER = logging.getLogger(__name__)


class AkuvoxCallApi:
    def __init__(self, client: AkuvoxApiClient) -> None:
        self.client = client

    async def get_call_status(self) -> str:
        try:
            response = await self.client.get("/api/call/status")
        except AkuvoxApiError as exc:
            LOGGER.warning("akuvox_call_status_failed reason=%s", exc)
            return "unknown"
        data = response.json_data.get("data") if isinstance(response.json_data, dict) else None
        status = data.get("Status") if isinstance(data, dict) else None
        return status.strip().lower() if isinstance(status, str) else "unknown"

    async def hangup_call(self) -> bool:
        try:
            await self.client.get("/api/call/hangup")
            LOGGER.info("akuvox_hangup_sent")
            return True
        except AkuvoxApiError as exc:
            LOGGER.warning("akuvox_hangup_failed reason=%s", exc)
            return False
