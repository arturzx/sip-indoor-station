from __future__ import annotations

from typing import Protocol


class CallController(Protocol):
    async def answer_current_call(self) -> bool:
        raise NotImplementedError

    async def reject_current_call(self) -> bool:
        raise NotImplementedError

    async def hangup_current_call(self) -> bool:
        raise NotImplementedError

    async def open_door(self, relay: int = 1) -> bool:
        raise NotImplementedError

    async def reboot(self) -> bool:
        raise NotImplementedError
