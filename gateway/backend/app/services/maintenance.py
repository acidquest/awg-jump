from __future__ import annotations

import asyncio


_db_ready = asyncio.Event()
_db_ready.set()
_reset_lock = asyncio.Lock()


async def wait_until_ready() -> None:
    await _db_ready.wait()


async def acquire_reset_lock() -> None:
    await _reset_lock.acquire()
    _db_ready.clear()


def release_reset_lock() -> None:
    _db_ready.set()
    _reset_lock.release()
