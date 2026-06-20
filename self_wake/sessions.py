"""Session index reader/resolver for self-wake plugin.

Reads session data from $HERMES_HOME/sessions/sessions.json and state.db
to resolve candidate target sessions.
"""

import json
import os


def read_sessions_index(hermes_home: str | None = None) -> list[dict]:
    """Read the sessions index from sessions.json.

    Args:
        hermes_home: Override HERMES_HOME path. Defaults to env or
            hermes_constants.get_hermes_home().

    Returns:
        List of session entry dicts.
    """
    # TODO: implement
    # - Use hermes_constants.get_hermes_home() if hermes_home is None
    # - Read $HERMES_HOME/sessions/sessions.json
    # - Parse and return entries
    return []


def query_state_db(hermes_home: str | None = None, **filters) -> list[dict]:
    """Query the state database for session information.

    Args:
        hermes_home: Override HERMES_HOME path.
        **filters: Filter criteria (session_id, session_key, platform, etc.)

    Returns:
        List of matching session dicts.
    """
    # TODO: implement
    # - Connect to $HERMES_HOME/state.db (read-only)
    # - Query sessions table with filters
    # - Return results
    return []


def resolve_target_session(
    session_key: str | None = None,
    session_id: str | None = None,
    platform: str | None = None,
    chat_id: str | None = None,
    thread_id: str | None = None,
) -> list[dict]:
    """Resolve candidate target sessions from index and/or state DB.

    Returns:
        List of matching sessions with origin info, match confidence,
        and whether host wake capability is present.
    """
    # TODO: implement resolution logic
    return []
