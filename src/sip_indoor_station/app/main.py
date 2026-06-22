from __future__ import annotations

import asyncio
import logging

from sip_indoor_station.app.http_server import AppHttpServer
from sip_indoor_station.api.state_api import StateApi
from sip_indoor_station.app.config import load_config
from sip_indoor_station.app.events import AppEvent, EventBus
from sip_indoor_station.calls.history import CallHistoryStore
from sip_indoor_station.sip.server import SipServer


def _normalize_vendor(value: str | None) -> str:
    return (value or "").strip().lower()


async def main() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_config()
    event_bus = EventBus()
    snapshot_provider = None
    call_history = None
    door_opener = None
    maintenance = None
    if config.api_enabled:
        vendor = _normalize_vendor(config.door_station_vendor)
        if not vendor:
            raise ValueError("API is enabled but DOOR_STATION_VENDOR is required")
        if not config.api_host:
            raise ValueError(f"API is enabled but API_HOST is required for vendor={vendor}")
        if vendor == "hikvision":
            from sip_indoor_station.vendor.hikvision.client import HikvisionIsapiClient
            from sip_indoor_station.vendor.hikvision.door import HikvisionDoorApi
            from sip_indoor_station.vendor.hikvision.maintenance import HikvisionMaintenanceApi
            from sip_indoor_station.vendor.hikvision.snapshot import HikvisionSnapshotProvider
            from sip_indoor_station.vendor.hikvision.models import IsapiClientConfig

            api_client = HikvisionIsapiClient(
                IsapiClientConfig(
                    host=config.api_host,
                    port=config.api_port,
                    username=config.api_username,
                    password=config.api_password,
                    use_https=config.api_use_https,
                    timeout_seconds=config.api_timeout_seconds,
                    verify_ssl=config.api_verify_ssl,
                )
            )
            snapshot_provider = HikvisionSnapshotProvider(api_client)
            door_opener = HikvisionDoorApi(api_client, relays_count=config.relays_count)
            maintenance = HikvisionMaintenanceApi(api_client)
        elif vendor == "dahua":
            from sip_indoor_station.vendor.dahua.client import DahuaApiClient
            from sip_indoor_station.vendor.dahua.door import DahuaDoorApi
            from sip_indoor_station.vendor.dahua.models import DahuaApiClientConfig
            from sip_indoor_station.vendor.dahua.snapshot import DahuaSnapshotProvider

            api_client = DahuaApiClient(
                DahuaApiClientConfig(
                    host=config.api_host,
                    port=config.api_port,
                    username=config.api_username,
                    password=config.api_password,
                    use_https=config.api_use_https,
                    timeout_seconds=config.api_timeout_seconds,
                    verify_ssl=config.api_verify_ssl,
                )
            )
            snapshot_provider = DahuaSnapshotProvider(api_client)
            door_opener = DahuaDoorApi(api_client, relays_count=config.relays_count)
        else:
            raise ValueError(f"Unsupported DOOR_STATION_VENDOR={vendor}")
    sip_server = SipServer(config, event_bus=event_bus, door_opener=door_opener, maintenance=maintenance)
    if config.call_history_enabled:
        call_history = (
            CallHistoryStore(config.call_history_db_path, config.call_history_days, event_bus, snapshot_provider)
            if config.call_history_enabled
            else None
        )
    state_api = StateApi(event_bus, sip_server, call_history)
    for registration in sip_server.registrations.active():
        await event_bus.publish(
            AppEvent(
                "registration_success",
                data={
                    "username": registration.username,
                    "contact_uri": registration.contact_uri,
                    "source": f"{registration.source_ip}:{registration.source_port}",
                    "user_agent": registration.user_agent,
                    "restored": True,
                },
            )
        )
    http_server = AppHttpServer(config, event_bus, sip_server.active_media_session, state_api)
    await sip_server.start()
    await http_server.start()
    try:
        await asyncio.Event().wait()
    finally:
        await http_server.stop()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
