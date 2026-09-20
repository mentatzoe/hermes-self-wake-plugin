"""Current-host delivery evidence is stronger than a mirror callback."""
from self_wake import cron_adapter


def frame(**changes):
    values = dict(delivered=True, timed_out=False, cleaned_delivery_content="result",
                  delivered_message_id="message", send_raw_response={}, opened_thread_id=None,
                  route_thread_id="thread", transport=None,
                  origin={"platform": "discord", "chat_id": "chat", "thread_id": "thread",
                          "user_id": "user"})
    values.update(changes)
    return values


def test_current_delivery_accepts_confirmed_text_route():
    target, reason = cron_adapter.current_delivery_target(frame(), "discord", "chat", "thread", "user")
    assert not reason
    assert target["thread_id"] == "thread"
    assert target["user_id"] == "user"


def test_current_delivery_rejects_uncertain_and_empty_effects():
    for changes in [dict(timed_out=True), dict(delivered_message_id=None),
                    dict(cleaned_delivery_content=""), dict(opened_thread_id="new")]:
        target, reason = cron_adapter.current_delivery_target(frame(**changes), "discord", "chat", "thread", "user")
        assert target is None and reason


def test_current_delivery_never_reuses_stale_raw_response_for_media_only():
    target, reason = cron_adapter.current_delivery_target(
        frame(cleaned_delivery_content="", send_raw_response={"thread_fallback": True}),
        "discord", "chat", "thread", "user")
    assert target is None


def test_current_delivery_carries_scope_and_effect_route():
    target, reason = cron_adapter.current_delivery_target(
        frame(origin={"platform": "discord", "chat_id": "chat", "thread_id": "thread",
                      "user_id": "user", "scope_id": "workspace", "profile": "beta"},
              send_raw_response={"thread_fallback": True}, route_thread_id="thread"),
        "discord", "chat", "thread", "user")
    assert target["thread_id"] is None
    assert target["scope_id"] == "workspace" and target["profile"] == "beta"


def test_telegram_channel_dm_topic_never_flattens_to_chat():
    target, reason = cron_adapter.current_delivery_target(
        frame(origin={"platform": "telegram", "chat_id": "chat"}, route_thread_id=None,
              route_metadata={"direct_messages_topic_id": "42"}),
        "telegram", "chat", "42", "user")
    assert not reason and target["thread_id"] == "42"
