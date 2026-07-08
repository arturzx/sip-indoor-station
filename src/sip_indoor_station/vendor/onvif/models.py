from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OnvifClientConfig:
    host: str
    port: int = 80
    username: str | None = None
    password: str | None = None
    timeout_seconds: float = 5.0
    verify_ssl: bool = False


@dataclass(frozen=True)
class OnvifSnapshotResponse:
    status: int
    content: bytes
    content_type: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300
