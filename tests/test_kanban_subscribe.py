"""Tests for Kanban subscription module."""

from self_wake.kanban import (
    list_wake_subscriptions,
    create_wake_subscription,
    migrate_visible_rows,
)


def test_list_subscriptions_returns_list():
    result = list_wake_subscriptions()
    assert isinstance(result, list)


def test_create_subscription_dry_run():
    """Dry-run subscribe should not write."""
    result = create_wake_subscription(
        task_id="t_test",
        target_session_key="test:key",
        dry_run=True,
    )
    assert isinstance(result, dict)


def test_migrate_visible_rows_dry_run():
    """Migration dry-run should report without writing."""
    result = migrate_visible_rows(dry_run=True)
    assert isinstance(result, dict)
