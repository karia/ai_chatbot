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
