import boto3
import pytest
from moto import mock_aws

from worker.store import Conflict, EventExpired, Store


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


def acquire(store, event="E", owner="one"):
    return store.acquire("T", event, "thread", owner, received_at=1000)


def test_acquire_is_atomic_and_excludes_competitors(state):
    store, table, now = state
    first = acquire(store)
    assert first["pk"] == "EVENT#T#E"
    assert first["lease_until"] == 1180
    assert first["attempt"] == 1
    assert first["expires_at"] == 1000 + 30 * 86400
    assert store.get_session("thread")["active_event_id"] == first["pk"]
    with pytest.raises(Conflict):
        acquire(store, owner="two")
    with pytest.raises(Conflict):
        acquire(store, event="other")
    assert store.get_event("T", "other") is None
    now[0] = 1180
    second = acquire(store, owner="two")
    assert second["attempt"] == 2
    with pytest.raises(Conflict):
        store.generated(first, "stale")
    assert store.get_event("T", "E")["owner"] == "two"


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
        store.acquire("T", "new", "thread", "two", received_at=now[0])
    assert store.get_event("T", "new") is None


def test_rate_slot_competition_and_channel_isolation(state):
    store, table, now = state
    assert store.reserve_post("T", "C") == 1001
    with pytest.raises(Conflict):
        store.reserve_post("T", "C")
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
    now[0] = 1180
    resumed = acquire(store, owner="two")
    assert resumed["status"] == "GENERATED"
    assert resumed["reply"] == event["reply"]
    assert resumed["expires_at"] == event["expires_at"]


def test_lease_expiry_and_invalid_transition_do_not_write(state):
    store, table, now = state
    event = acquire(store)
    with pytest.raises(Conflict):
        store.complete(event, "123.456")
    now[0] = 1180
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
    event = store.defer(event, 1300)
    now[0] = 1180
    with pytest.raises(Conflict):
        acquire(store, owner="two")
    now[0] = 1300
    resumed = acquire(store, owner="two")
    assert resumed["phase"] == "EXECUTING"
    assert resumed["retry_at"] == 1300
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


def test_fractional_lease_expires_after_exactly_180_seconds(state):
    store, table, now = state
    now[0] = 1000.9
    event = acquire(store)
    now[0] = 1180.1
    with pytest.raises(Conflict):
        acquire(store, owner="two")
    now[0] = 1180.9
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
    deferred = store.defer(event, time.time() + 300.5)
    assert deferred["retry_at"] == Decimal("1300.5")
    assert store.get_event("T", "E")["retry_at"] == Decimal("1300.5")
    now[0] = 1300.4
    with pytest.raises(Conflict):
        acquire(store, owner="two")
    now[0] = 1300.5
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
        "level": "WARNING", "operation": "state_conflict"
    }
    assert store.get_event("T", "E") is None
