"""Native cursor acknowledgement uses real SQLite and never moves backwards."""
from contextlib import contextmanager
import sqlite3
import sys
from types import ModuleType

from self_wake import compat_shim as shim


def test_native_cursor_ack_is_monotonic_and_generation_bound(tmp_path, monkeypatch):
    path = tmp_path / 'notifications.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE kanban_notify_subs (task_id TEXT, platform TEXT, chat_id TEXT, thread_id TEXT, created_at INTEGER, last_event_id INTEGER)')
        conn.execute("INSERT INTO kanban_notify_subs VALUES ('t','discord','parent','',1,10)")
    kb = ModuleType('hermes_cli.kanban_db')
    kb.connect = lambda **kwargs: sqlite3.connect(path)
    @contextmanager
    def write_txn(conn):
        with conn:
            yield
    kb.write_txn = write_txn
    cli = ModuleType('hermes_cli')
    cli.kanban_db = kb
    monkeypatch.setitem(sys.modules, 'hermes_cli', cli)
    monkeypatch.setitem(sys.modules, 'hermes_cli.kanban_db', kb)
    sub = {'task_id': 't', 'platform': 'discord', 'chat_id': 'parent', 'thread_id': '', 'created_at': 1}
    shim._advance_native(sub, 5, 'test')
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT last_event_id FROM kanban_notify_subs').fetchone()[0] == 10
    shim._advance_native(sub, 11, 'test')
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT last_event_id FROM kanban_notify_subs').fetchone()[0] == 11
        conn.execute('UPDATE kanban_notify_subs SET created_at=2,last_event_id=1')
    shim._advance_native(sub, 12, 'test')
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT last_event_id FROM kanban_notify_subs').fetchone()[0] == 1
