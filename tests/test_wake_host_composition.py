"""Current-host object composition and native busy-retry regressions."""
import asyncio
import sqlite3
import threading

from self_wake import compat_shim as shim
from tests.test_wake_dispatch import wake_world, _wake, _receipt_status  # noqa: F401


class AsyncProxy:
    """Same offloaded-call contract as Hermes AsyncSessionDB, no board writes."""
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        attr = getattr(self._db, name)
        if not callable(attr):
            return attr

        async def offloaded(*args, **kwargs):
            return await asyncio.to_thread(attr, *args, **kwargs)

        return offloaded


def test_async_session_db_records_and_dedupes(wake_world):
    runner, adapter, entry, db = wake_world
    # The historical sync fake is single-threaded. Match the real host's
    # cross-thread DB connection/lock before exercising its proxy contract.
    runner._session_db._conn.close()
    runner._session_db._conn = sqlite3.connect(db, check_same_thread=False)
    runner._session_db._conn.row_factory = sqlite3.Row
    runner._session_db._lock = threading.RLock()
    runner._session_db = AsyncProxy(runner._session_db)
    first = _wake(runner, entry)
    assert first["status"] == "dispatched"
    assert _receipt_status(db, first["receipt_id"]) == "dispatched"
    assert _wake(runner, entry)["status"] == "deduped"
    assert len(adapter.handled) == 1


# Native busy-to-idle coverage lives in test_native_admission.py. Its
# receiver persists the exact event ID; this legacy transport fake only
# appends to `handled`, which cannot establish native acceptance.


def test_task_payload_cannot_act_as_gateway_control(wake_world):
    runner, adapter, entry, db = wake_world
    _wake(runner, entry, payload="/approve")
    assert adapter.handled[0].allow_gateway_control is False
