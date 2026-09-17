import boto3
import pytest
from moto import mock_aws

from worker.store import (
    Conflict,
    EventExpired,
    PostingSlotUnavailable,
    SessionBusy,
    SessionStopped,
    Store,
)


@pytest.fixture
def state(monkeypatch):
    with mock_aws():
        table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="test-table",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        now = [1000]
        monkeypatch.setattr("worker.store.time.time", lambda: now[0])
        yield Store(), table, now


def acquire(store, event="E", owner="one", remaining_ms=300_000):
    return store.acquire("T", event, "thread", owner, received_at=1000, remaining_ms=remaining_ms)


def test_acquire_is_atomic_and_excludes_competitors(state):
    store, table, now = state
    first = acquire(store)
    assert first["pk"] == "EVENT#T#E"
    assert first["lease_until"] == 1330
    assert first["attempt"] == 1
    assert first["expires_at"] == 1000 + 30 * 86400
    assert store.get_session("thread")["active_event_id"] == first["pk"]
    with pytest.raises(Conflict):
        acquire(store, owner="two")
    with pytest.raises(Conflict):
        acquire(store, event="other")
    assert store.get_event("T", "other") is None
    now[0] = 1330
    second = acquire(store, owner="two")
    assert second["attempt"] == 2
    with pytest.raises(Conflict):
        store.generated(first, "stale")
    assert store.get_event("T", "E")["owner"] == "two"


@pytest.mark.parametrize("remaining_ms", [300_000, 900_000, 300_001])
def test_lease_exceeds_remaining_lambda_time(state, remaining_ms):
    store, table, now = state
    event = acquire(store, remaining_ms=remaining_ms)
    assert event["lease_until"] - now[0] > remaining_ms / 1000


def test_limit_notice_does_not_increment_answer_count(state):
    store, table, now = state
    event = acquire(store)
    table.update_item(
        Key={"pk": "SESSION#thread"},
        UpdateExpression="SET answer_count = :count",
        ExpressionAttributeValues={":count": 100},
    )
    event = store.generated(event, "新しいスレッドを開始してください。", answer_limit_notice=True)
    store.complete(store.posting(event), "123.456")
    assert store.get_session("thread")["answer_count"] == 100
    assert store.get_event("T", "E")["status"] == "COMPLETED"


def test_acquire_distinguishes_busy_and_stopped_sessions(state):
    store, table, now = state
    event = acquire(store)

    with pytest.raises(SessionBusy):
        acquire(store, event="other")

    store.needs_review(event, "manual_review")
    with pytest.raises(SessionStopped):
        acquire(store, event="later")


def test_completion_and_redelivery_count_once(state):
    store, table, now = state
    event = acquire(store)
    event = store.generated(event, "answer")
    event = store.posting(event)
    store.complete(event, "123.456")
    with pytest.raises(Conflict):
        store.complete(event, "123.456")
    completed = acquire(store, owner="two")
    assert completed["status"] == "COMPLETED"
    assert completed["slack_ts"] == "123.456"
    session = store.get_session("thread")
    assert session["answer_count"] == 1
    assert "active_event_id" not in session
    assert "expires_at" not in session


def test_expiration_is_checked_without_ttl_deletion(state):
    store, table, now = state
    event = acquire(store)
    now[0] = event["expires_at"]
    assert store.get_event("T", "E") is None
    with pytest.raises(EventExpired):
        acquire(store)
    with pytest.raises(Conflict):
        store.generated(event, "late")


@pytest.mark.parametrize("deleted", [False, True])
def test_stopped_session_survives_event_expiration(state, deleted):
    store, table, now = state
    event = acquire(store)
    store.needs_review(event, "memory_unknown")
    now[0] = event["expires_at"]
    if deleted:
        table.delete_item(Key={"pk": event["pk"]})
    assert store.get_event("T", "E") is None
    session = store.get_session("thread")
    assert session["stop_reason"] == "memory_unknown"
    assert "expires_at" not in session
    with pytest.raises(Conflict):
        store.acquire("T", "new", "thread", "two", received_at=now[0], remaining_ms=300_000)
    assert store.get_event("T", "new") is None


def test_rate_slot_competition_and_channel_isolation(state):
    store, table, now = state
    assert store.reserve_post("T", "C") == 1001
    with pytest.raises(PostingSlotUnavailable) as caught:
        store.reserve_post("T", "C")
    assert caught.value.next_post_at == 1001
    assert store.reserve_post("T", "other") == 1001
    now[0] = 1001
    assert store.reserve_post("T", "C") == 1002
    assert table.get_item(Key={"pk": "RATE#T#C"})["Item"]["next_post_at"] == 1002


def test_completion_rolls_back_if_session_changed(state):
    store, table, now = state
    event = store.posting(store.generated(acquire(store), "answer"))
    table.update_item(
        Key={"pk": "SESSION#thread"},
        UpdateExpression="SET active_event_id = :other",
        ExpressionAttributeValues={":other": "EVENT#T#other"},
    )
    with pytest.raises(Conflict):
        store.complete(event, "123.456")
    assert store.get_event("T", "E")["status"] == "POSTING"
    assert store.get_session("thread")["answer_count"] == 0


def test_resume_retains_reply_and_rejects_wrong_owner_or_attempt(state):
    store, table, now = state
    event = store.generated(acquire(store), "あ" * 30000)
    assert len(event["reply"].encode("utf-8")) <= 65536
    for changes in ({"owner": "other"}, {"attempt": 2}):
        with pytest.raises(Conflict):
            store.posting({**event, **changes})
    now[0] = 1330
    resumed = acquire(store, owner="two")
    assert resumed["status"] == "GENERATED"
    assert resumed["reply"] == event["reply"]
    assert resumed["expires_at"] == event["expires_at"]


def test_lease_expiry_and_invalid_transition_do_not_write(state):
    store, table, now = state
    event = acquire(store)
    with pytest.raises(Conflict):
        store.complete(event, "123.456")
    now[0] = 1330
    with pytest.raises(Conflict):
        store.generated(event, "late")
    assert store.get_event("T", "E")["status"] == "RUNNING"


@pytest.mark.parametrize("operation", ["acquire", "complete", "reserve"])
def test_competing_write_after_read_is_rejected(state, monkeypatch, operation):
    store, table, now = state
    competitor = Store()
    if operation == "complete":
        event = store.posting(store.generated(acquire(store), "answer"))
        run = lambda target: target.complete(event, "123.456")
    elif operation == "reserve":
        run = lambda target: target.reserve_post("T", "C")
    else:
        run = lambda target: acquire(target)
    transact = store.client.transact_write_items

    def race(**kwargs):
        run(competitor)
        return transact(**kwargs)

    monkeypatch.setattr(store.client, "transact_write_items", race)
    with pytest.raises(Conflict):
        run(store)
    if operation == "complete":
        assert store.get_session("thread")["answer_count"] == 1


def test_fractional_post_slots_are_a_full_second_apart(state):
    store, table, now = state
    now[0] = 1000.9
    store.reserve_post("T", "C")
    now[0] = 1001.1
    with pytest.raises(Conflict):
        store.reserve_post("T", "C")
    now[0] = 1001.9
    store.reserve_post("T", "C")


def test_phase_retry_time_and_memory_mapping_survive_resume(state):
    store, table, now = state
    event = acquire(store)
    store.bind_memory("thread", "memory-session")
    store.bind_memory("thread", "memory-session")
    with pytest.raises(Conflict):
        store.bind_memory("thread", "different-memory-session")
    event = store.started(event)
    event = store.defer(event, 1400)
    now[0] = 1330
    with pytest.raises(Conflict):
        acquire(store, owner="two")
    now[0] = 1400
    resumed = acquire(store, owner="two")
    assert resumed["phase"] == "EXECUTING"
    assert resumed["retry_at"] == 1400
    session = store.get_session("thread")
    assert session["memory_session_id"] == "memory-session"
    assert session["active_event_id"] == event["pk"]


def test_logs_are_structured_and_level_controlled(state, monkeypatch, capsys):
    import json

    store, table, now = state
    event = acquire(store)
    event = store.generated(event, "private-reply-content")
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[-1]["status"] == "GENERATED"
    assert records[-1]["level"] == "INFO"
    assert "private-reply-content" not in str(records)
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    store.posting(event)
    assert capsys.readouterr().out == ""


def test_fractional_lease_expires_after_remaining_time_plus_margin(state):
    store, table, now = state
    now[0] = 1000.9
    event = acquire(store)
    now[0] = 1330.1
    with pytest.raises(Conflict):
        acquire(store, owner="two")
    now[0] = 1330.9
    with pytest.raises(Conflict):
        store.generated(event, "late")
    assert acquire(store, owner="two")["attempt"] == 2


def test_session_race_rolls_back_event_acquisition(state, monkeypatch):
    store, table, now = state
    transact = store.client.transact_write_items

    def race(**kwargs):
        acquire(Store(), event="other")
        return transact(**kwargs)

    monkeypatch.setattr(store.client, "transact_write_items", race)
    with pytest.raises(Conflict):
        acquire(store)
    assert store.get_event("T", "E") is None
    assert store.get_session("thread")["active_event_id"] == "EVENT#T#other"


def test_isolation_rolls_back_session_stop_on_stale_event(state, monkeypatch):
    store, table, now = state
    event = acquire(store)
    transact = store.client.transact_write_items

    def race(**kwargs):
        Store().started(event)
        return transact(**kwargs)

    monkeypatch.setattr(store.client, "transact_write_items", race)
    with pytest.raises(Conflict):
        store.needs_review(event, "unknown")
    assert "stop_reason" not in store.get_session("thread")
    assert store.get_event("T", "E")["status"] == "RUNNING"


def test_expired_deleted_event_cannot_be_recreated_with_original_receipt(state):
    store, table, now = state
    event = acquire(store)
    table.delete_item(Key={"pk": event["pk"]})
    now[0] = event["expires_at"]
    with pytest.raises(EventExpired):
        acquire(store)


def test_definite_post_rejection_can_resume_after_retry_time(state):
    store, table, now = state
    event = store.posting(store.generated(acquire(store), "answer"))
    store.defer(event, 1400)
    now[0] = 1400
    resumed = acquire(store, owner="two")
    assert resumed["status"] == "GENERATED"
    assert resumed["reply"] == "answer"
    store.complete(store.posting(resumed), "123.456")
    assert store.get_session("thread")["answer_count"] == 1


def test_storage_failures_are_not_reported_as_contention(state, monkeypatch):
    from botocore.exceptions import ClientError

    store, table, now = state

    def fail(**kwargs):
        raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "TransactWriteItems")

    monkeypatch.setattr(store.client, "transact_write_items", fail)
    with pytest.raises(ClientError):
        acquire(store)
    assert store.get_event("T", "E") is None


def test_isolation_is_logged_at_error_level(state, monkeypatch, capsys):
    import json

    store, table, now = state
    event = acquire(store)
    capsys.readouterr()
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    store.needs_review(event, "memory_unknown")
    record = json.loads(capsys.readouterr().out)
    assert record["level"] == "ERROR"
    assert record["status"] == "NEEDS_REVIEW"


def test_defer_accepts_float_retry_time(state):
    from decimal import Decimal
    import time

    store, table, now = state
    event = acquire(store)
    deferred = store.defer(event, time.time() + 350.5)
    assert deferred["retry_at"] == Decimal("1350.5")
    assert store.get_event("T", "E")["retry_at"] == Decimal("1350.5")
    now[0] = 1350.4
    with pytest.raises(Conflict):
        acquire(store, owner="two")
    now[0] = 1350.5
    assert acquire(store, owner="two")["attempt"] == 2


def test_transaction_conflict_is_retryable_and_logged_as_warning(state, monkeypatch, capsys):
    import json
    from botocore.exceptions import ClientError

    store, table, now = state
    error = ClientError(
        {
            "Error": {"Code": "TransactionCanceledException"},
            "CancellationReasons": [{"Code": "None"}, {"Code": "TransactionConflict"}],
        },
        "TransactWriteItems",
    )

    def fail(**kwargs):
        raise error

    monkeypatch.setattr(store.client, "transact_write_items", fail)
    with pytest.raises(Conflict) as caught:
        acquire(store)
    assert caught.value.__cause__ is error
    assert json.loads(capsys.readouterr().out) == {
        "level": "WARNING", "component": "store", "operation": "state_conflict",
        "correlation_id": "E", "state": "RUNNING", "status": "RUNNING", "attempt": 1,
    }
    assert store.get_event("T", "E") is None


def test_session_update_times_preserve_fractional_seconds(state):
    from decimal import Decimal

    store, table, now = state
    now[0] = 1000.25
    event = acquire(store)
    assert store.get_session("thread")["updated_at"] == Decimal("1000.25")
    now[0] = 1000.5
    store.bind_memory("thread", "memory-session")
    assert store.get_session("thread")["updated_at"] == Decimal("1000.5")
    event = store.posting(store.generated(event, "answer"))
    now[0] = 1000.75
    store.complete(event, "123.456")
    assert store.get_session("thread")["updated_at"] == Decimal("1000.75")


def test_split_posts_are_reserved_and_saved_in_order(state):
    store, table, now = state
    event = store.generated(acquire(store), "abcdefgh")
    event = store.prepare_posts(event, [4, 8])
    event = store.start_post(event, 0, "T", "C")
    assert event["posting_part"] == 0
    assert table.get_item(Key={"pk": "RATE#T#C"})["Item"]["next_post_at"] == 1001
    other = store.acquire("T", "other", "other-thread", "one", received_at=1000, remaining_ms=300_000)
    other = store.prepare_posts(store.generated(other, "x"), [1])
    with pytest.raises(Conflict):
        store.start_post(other, 0, "T", "C")
    event = store.posted(event, 0, "1.0")
    assert event["slack_parts"] == [{"end": 4, "ts": "1.0"}, {"end": 8}]
    assert "posting_part" not in event
    now[0] = 1001
    event = store.start_post(event, 1, "T", "C")
    event = store.posted(event, 1, "2.0")
    store.complete(event, "2.0")
    assert store.get_event("T", "E")["slack_parts"][-1]["ts"] == "2.0"


def test_split_post_state_rejects_skips_and_can_defer_a_definite_rejection(state):
    store, table, now = state
    event = store.prepare_posts(store.generated(acquire(store), "abcdefgh"), [4, 8])
    with pytest.raises(Conflict):
        store.start_post(event, 1, "T", "C")
    event = store.start_post(event, 0, "T", "C")
    with pytest.raises(Conflict):
        store.start_post(event, 0, "T", "other")
    deferred = store.defer_post(event, 1400)
    assert deferred["status"] == "GENERATED"
    assert deferred["retry_at"] == 1400
    assert "posting_part" not in deferred
    now[0] = 1400
    resumed = acquire(store, owner="two")
    assert resumed["slack_parts"] == [{"end": 4}, {"end": 8}]


def test_split_plan_cannot_change_after_posting_starts(state):
    store, table, now = state
    event = store.prepare_posts(store.generated(acquire(store), "abcdefgh"), [4, 8])
    event = store.defer_post(store.start_post(event, 0, "T", "C"), 1380)
    now[0] = 1380
    event = acquire(store, owner="two")
    with pytest.raises(Conflict):
        store.prepare_posts(event, [8])
