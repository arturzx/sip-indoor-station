from __future__ import annotations

from sip_indoor_station.sip.server import Snapshot
from sip_indoor_station.vendor.akuvox.client import AkuvoxApiClient


class AkuvoxSnapshotProvider:
    def __init__(self, client: AkuvoxApiClient, path: str = "/picture.jpg") -> None:
        self.client = client
        self.path = path

    async def capture_snapshot(self) -> Snapshot | None:
        response = await self.client.get_snapshot(self.path)
        if not response.content:
            return None
        return Snapshot(response.content, response.content_type or "image/jpeg")