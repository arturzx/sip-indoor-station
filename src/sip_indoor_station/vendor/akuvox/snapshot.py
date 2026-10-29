from __future__ import annotations

import logging

from sip_indoor_station.vendor.akuvox.client import AkuvoxApiClient
from sip_indoor_station.vendor.onvif.models import OnvifClientConfig
from sip_indoor_station.vendor.onvif.errors import OnvifProviderError
from sip_indoor_station.vendor.onvif.snapshot import (
    OnvifClientFactory,
    OnvifSnapshotProvider,
    OnvifSnapshotResponse,
    SnapshotFetcher,
)

LOGGER = logging.getLogger(__name__)


class AkuvoxSnapshotProvider(OnvifSnapshotProvider):
    def __init__(
        self,
        client: AkuvoxApiClient,
        *,
        username: str | None = None,
        password: str | None = None,
        profile_index: int = 0,
        client_factory: OnvifClientFactory | None = None,
        snapshot_fetcher: SnapshotFetcher | None = None,
    ) -> None:
        self.client = client
        super().__init__(
            OnvifClientConfig(
                host=client.config.host,
                port=client.config.port,
                username=username or client.config.username,
                password=password or client.config.password,
                timeout_seconds=client.config.timeout_seconds,
                verify_ssl=client.config.verify_ssl,
            ),
            profile_index=profile_index,
            client_factory=client_factory,
            snapshot_fetcher=snapshot_fetcher,
        )

    async def fetch_snapshot(self, uri: str) -> OnvifSnapshotResponse:
        # The device redirects plain HTTP to its MJPEG stream and drops the credentials,
        # while the same path over HTTPS returns a single JPEG.
        if uri.startswith("http://"):
            try:
                return await super().fetch_snapshot("https://" + uri[len("http://"):])
            except OnvifProviderError as exc:
                LOGGER.debug("akuvox_https_snapshot_failed reason=%s", exc)
        return await super().fetch_snapshot(uri)
