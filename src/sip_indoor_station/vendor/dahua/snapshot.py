from __future__ import annotations

from sip_indoor_station.sip.server import Snapshot
from sip_indoor_station.vendor.dahua.client import DahuaApiClient


class DahuaSnapshotProvider:
    def __init__(self, client: DahuaApiClient, channel: int = 1) -> None:
        self.client = client
        self.channel = channel

    async def capture_snapshot(self) -> Snapshot | None:
        response = await self.client.get_snapshot(self.channel)
        if not response.content:
            return None
        return Snapshot(response.content, response.content_type or "image/jpeg")
