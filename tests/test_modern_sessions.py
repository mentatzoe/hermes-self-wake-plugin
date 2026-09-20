"""Native routing index discovery must not depend on the JSON mirror."""
import json
import sqlite3

from self_wake import sessions, capabilities


def routing_db(home, entries):
    with sqlite3.connect(home / "state.db") as db:
        db.execute("CREATE TABLE gateway_routing (scope TEXT, session_key TEXT, entry_json TEXT)")
        for scope, key, entry in entries:
            db.execute("INSERT INTO gateway_routing VALUES (?,?,?)", (scope, key, json.dumps(entry)))


def test_native_index_without_json_mirror(hermes_home):
    cache = sessions.read_current_session_cache(hermes_home)
    key, entry = next(iter(cache.items()))
    (hermes_home / "sessions" / "sessions.json").unlink()
    routing_db(hermes_home, [(str((hermes_home / "sessions").resolve()), key, entry)])
    assert sessions.resolve_target_session(session_id=entry["session_id"], hermes_home=hermes_home)[0]["session_key"] == key
    assert capabilities._probe_session_resolver_readable(hermes_home)["available"]


def test_native_index_wins_and_excludes_other_profile(hermes_home):
    cache = sessions.read_current_session_cache(hermes_home)
    key, entry = next(iter(cache.items()))
    current = dict(entry, session_id="current-session")
    foreign = dict(entry, session_key="foreign", session_id="foreign-session")
    routing_db(hermes_home, [(str((hermes_home / "sessions").resolve()), key, current),
                            ("/other/profile/sessions", "foreign", foreign)])
    assert sessions.resolve_target_session(session_key=key, hermes_home=hermes_home)[0]["session_id"] == "current-session"
    assert not sessions.query_host_sessions(session_id="foreign-session", hermes_home=hermes_home)


def test_exact_key_still_checks_other_filters(hermes_home):
    key = next(iter(sessions.read_current_session_cache(hermes_home)))
    assert not sessions.resolve_target_session(session_key=key, session_id="wrong-session", hermes_home=hermes_home)
