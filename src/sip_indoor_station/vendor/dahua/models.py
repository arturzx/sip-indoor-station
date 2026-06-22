from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DahuaApiClientConfig:
    host: str
    port: int = 80
    username: str | None = None
    password: str | None = None
    use_https: bool = False
    timeout_seconds: float = 5.0
    verify_ssl: bool = False


@dataclass(frozen=True)
class DahuaResponse:
    status: int
    text: str
    json_data: Any | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


@dataclass(frozen=True)
class DahuaSnapshotResponse:
    status: int
    content: bytes
    content_type: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300
