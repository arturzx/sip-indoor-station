from __future__ import annotations

from sip_indoor_station.vendor.dnake.client import DnakeApiClient
from sip_indoor_station.vendor.onvif.models import OnvifClientConfig
from sip_indoor_station.vendor.onvif.snapshot import OnvifCameraFactory, OnvifSnapshotProvider, SnapshotFetcher


class DnakeSnapshotProvider(OnvifSnapshotProvider):
    def __init__(
        self,
        client: DnakeApiClient,
        *,
        profile_index: int = 0,
        camera_factory: OnvifCameraFactory | None = None,
        snapshot_fetcher: SnapshotFetcher | None = None,
    ) -> None:
        self.client = client
        super().__init__(
            OnvifClientConfig(
                host=client.config.host,
                port=client.config.port,
                username=client.config.username,
                password=client.config.password,
                timeout_seconds=client.config.timeout_seconds,
                verify_ssl=client.config.verify_ssl,
            ),
            profile_index=profile_index,
            camera_factory=camera_factory,
            snapshot_fetcher=snapshot_fetcher,
        )
