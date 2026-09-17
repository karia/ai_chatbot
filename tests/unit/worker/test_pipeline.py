import json

import pytest

from ingress.events import normalize
from worker import app, pipeline
from worker.conversation import MAX_ANSWERS_PER_THREAD
from worker.pipeline import (
    MAX_MESSAGE_BYTES,
    MESSAGE_FIELDS,
    InvalidMessage,
    process,
    validate,
)
from worker.slack_reply import PermanentSlackError, RetryableSlackError
from worker.store import Conflict, EventExpired, SessionBusy, SessionStopped

TEST_REPLY = "answer"


@pytest.fixture(autouse=True)
def mock_conversation(monkeypatch):
    monkeypatch.setattr(pipeline, "generate", lambda message: TEST_REPLY)


class Context:
    aws_request_id = "request-test"

    def __init__(self, remaining=300_000):
        self.remaining = remaining

    def get_remaining_time_in_millis(self):
        return self.remaining


class Store:
    def __init__(self, saved=None, error=None, context=None):
        self.saved = saved or {"status": "RUNNING"}
        self.error = error
        self.context = context
        self.calls = []

    def acquire(self, *args, **kwargs):
        self.calls.append(("acquire", args, kwargs))
        if self.context:
            self.context.remaining = 0
        if self.error:
            raise self.error
        return self.saved

    def started(self, event):
        self.calls.append(("started",))
        return {**event, "phase": "EXECUTING"}

    def generated(self, event, reply, *, answer_limit_notice=False):
        self.calls.append(("generated", reply))
        return {**event, "status": "GENERATED", "reply": reply, "answer_limit_notice": answer_limit_notice}

    def needs_review(self, event, failure):
        self.calls.append(("needs_review", failure))
        return {**event, "status": "NEEDS_REVIEW", "failure": failure}


class Adapter:
    def __init__(self, error=None):
        self.error = error
        self.calls = []
        self.event = None

    def send(self, event, team, channel, thread_ts, context):
        self.calls.append((event, team, channel, thread_ts))
        if self.error:
            if isinstance(self.error, PermanentSlackError):
                self.event = {**event, "status": "NEEDS_REVIEW"}
            raise self.error
        self.event = {**event, "status": "COMPLETED"}
        return self.event


@pytest.fixture
def message():
    return {
        "schema_version": 1,
        "event_id": "EvTEST",
        "team_id": "TTEST",
        "api_app_id": "ATEST",
        "channel_id": "CTEST",
        "user_id": "UTEST",
        "thread_ts": "1800000000.000001",
        "message_ts": "1800000000.000002",
        "text": "hello",
        "file_ids": ["FTEST"],
        "received_at": 1_800_000_000,
    }


def sqs(message):
    return {"Records": [{"body": json.dumps(message)}]}


def test_ingress_output_matches_worker_contract(message):
    payload = {
        "type": "event_callback",
        "event_id": message["event_id"],
        "team_id": message["team_id"],
        "api_app_id": message["api_app_id"],
        "event": {
            "type": "app_mention",
            "channel": message["channel_id"],
            "user": message["user_id"],
            "thread_ts": message["thread_ts"],
            "ts": message["message_ts"],
            "text": message["text"],
            "files": [{"id": "FTEST"}],
        },
    }
    ingress_message = normalize(
        payload, message["team_id"], message["api_app_id"], message["received_at"]
    )

    assert set(ingress_message) == MESSAGE_FIELDS
    assert process(sqs(ingress_message), Context(), Store(), Adapter()) == "completed"


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.pop("event_id"),
        lambda value: value.update(schema_version=2),
        lambda value: value.update(thread_ts=1.0),
        lambda value: value.update(message_ts="invalid"),
        lambda value: value.update(text=None),
        lambda value: value.update(file_ids=[""]),
        lambda value: value.update(file_ids={}),
        lambda value: value.update(received_at=True),
    ],
)
def test_invalid_messages_are_isolated_without_retry(message, change):
    change(message)
    store = Store()
    adapter = Adapter()

    assert process(sqs(message), Context(), store, adapter) == "isolated"
    assert store.calls == []
    assert adapter.calls == []


def test_unknown_message_fields_are_ignored(message):
    message["future_field"] = {"nested": "value"}

    assert process(sqs(message), Context(), Store(), Adapter()) == "completed"


@pytest.mark.parametrize(
    "event",
    [
        {"Records": []},
        {"Records": [{"body": 1}]},
        {"Records": [{"body": "x" * (MAX_MESSAGE_BYTES + 1)}]},
        {"Records": [{"body": "\ud800"}]},
        {"Records": [{"body": "[" * 10_000 + "]" * 10_000}]},
    ],
)
def test_invalid_sqs_record_is_isolated(event):
    assert process(event, Context(), Store(), Adapter()) == "isolated"
    with pytest.raises(InvalidMessage):
        validate(event)


def test_completed_redelivery_succeeds_without_posting(message):
    adapter = Adapter()

    result = process(sqs(message), Context(), Store({"status": "COMPLETED"}), adapter)

    assert result == "duplicate"
    assert adapter.calls == []


def test_completed_redelivery_does_not_create_slack_adapter(message, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "SlackReplyAdapter",
        lambda **kwargs: pytest.fail("Slack adapter must not be created"),
    )

    assert process(sqs(message), Context(), Store({"status": "COMPLETED"})) == "duplicate"


def test_active_lease_is_retried_without_posting(message):
    adapter = Adapter()

    with pytest.raises(Conflict):
        process(sqs(message), Context(), Store(error=Conflict("leased")), adapter)

    assert adapter.calls == []


def test_expired_event_is_isolated_without_posting(message):
    adapter = Adapter()

    assert (
        process(sqs(message), Context(), Store(error=EventExpired()), adapter)
        == "isolated"
    )
    assert adapter.calls == []


def test_stopped_session_is_logged_as_error_and_retried_without_external_call(
    message, capsys
):
    adapter = Adapter()

    with pytest.raises(SessionStopped):
        process(sqs(message), Context(), Store(error=SessionStopped()), adapter)

    assert adapter.calls == []
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[-1]["state"] == "session_stopped"
    assert records[-1]["level"] == "ERROR"
    assert records[-1]["correlation_id"] == message["event_id"]
    assert records[-1]["error_class"] == "SessionStopped"
    assert all(record["state"] != "stage_failed" for record in records)


def test_busy_session_is_logged_as_warning_and_retried_without_external_call(
    message, capsys
):
    adapter = Adapter()

    with pytest.raises(SessionBusy):
        process(sqs(message), Context(), Store(error=SessionBusy()), adapter)

    assert adapter.calls == []
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[-1]["state"] == "session_busy"
    assert records[-1]["level"] == "WARNING"
    assert all(record["state"] != "stage_failed" for record in records)


@pytest.mark.parametrize(
    "saved,expected_calls",
    [
        ({"status": "RUNNING"}, ["acquire", "started", "generated"]),
        (
            {"status": "GENERATED", "reply": TEST_REPLY},
            ["acquire"],
        ),
        (
            {
                "status": "POSTING",
                "reply": TEST_REPLY,
                "slack_parts": [{"end": len(TEST_REPLY), "ts": "1.0"}],
            },
            ["acquire"],
        ),
    ],
)
def test_safe_persistence_boundaries_resume(message, saved, expected_calls):
    store = Store(saved)
    adapter = Adapter()

    assert process(sqs(message), Context(), store, adapter) == "completed"

    assert [call[0] for call in store.calls] == expected_calls
    assert len(adapter.calls) == 1


def test_interrupted_execution_is_isolated_without_posting(message):
    store = Store({"status": "RUNNING", "phase": "EXECUTING"})
    adapter = Adapter()

    assert process(sqs(message), Context(), store, adapter) == "isolated"

    assert [call[0] for call in store.calls] == ["acquire", "needs_review"]
    assert store.calls[-1][1] == "execution_unknown"
    assert adapter.calls == []


def test_memory_failure_never_marks_generated(message, monkeypatch):
    store = Store()
    adapter = Adapter()
    def fail(_):
        raise TimeoutError("Memory timed out")
    monkeypatch.setattr(pipeline, "generate", fail)

    with pytest.raises(TimeoutError):
        process(sqs(message), Context(), store, adapter)
    assert [call[0] for call in store.calls] == ["acquire", "started"]
    assert adapter.calls == []


def test_answer_limit_posts_notice_and_completes_without_model(message, monkeypatch):
    store = Store({"status": "RUNNING", "answer_count": MAX_ANSWERS_PER_THREAD})
    adapter = Adapter()
    monkeypatch.setattr(pipeline, "generate", lambda _: pytest.fail("model invoked"))

    assert process(sqs(message), Context(), store, adapter) == "completed"
    assert [call[0] for call in store.calls] == ["acquire", "generated"]
    assert adapter.calls[0][0]["reply"] == pipeline.ANSWER_LIMIT_NOTICE
    assert adapter.calls[0][0]["answer_limit_notice"] is True


def test_last_allowed_answer_still_generates(message):
    store = Store({"status": "RUNNING", "answer_count": MAX_ANSWERS_PER_THREAD - 1})
    assert process(sqs(message), Context(), store, Adapter()) == "completed"
    assert [call[0] for call in store.calls] == ["acquire", "started", "generated"]


@pytest.mark.parametrize(
    "saved",
    [
        {"status": "RUNNING", "phase": "UNKNOWN"},
        {"status": "UNKNOWN"},
    ],
)
def test_unknown_persisted_state_is_retried_without_posting(message, saved):
    adapter = Adapter()

    with pytest.raises(Conflict):
        process(sqs(message), Context(), Store(saved), adapter)

    assert adapter.calls == []


@pytest.mark.parametrize("error", [RetryableSlackError("retry"), Conflict("posting slot")])
def test_retryable_delivery_failures_are_retried(message, error):
    with pytest.raises(type(error)):
        process(sqs(message), Context(), Store(), Adapter(error))


def test_permanent_delivery_failure_is_acknowledged_after_isolation(message):
    adapter = Adapter(PermanentSlackError("rejected"))

    assert process(sqs(message), Context(), Store(), adapter) == "isolated"
    assert adapter.event["status"] == "NEEDS_REVIEW"


@pytest.mark.parametrize(
    "store,adapter,final_state",
    [
        (Store(error=EventExpired()), Adapter(), "event_expired"),
        (
            Store(),
            Adapter(PermanentSlackError("rejected")),
            "delivery_isolated",
        ),
    ],
)
def test_expected_isolation_has_one_result_log(
    message, capsys, store, adapter, final_state
):
    assert process(sqs(message), Context(), store, adapter) == "isolated"

    states = [
        json.loads(line)["state"] for line in capsys.readouterr().out.splitlines()
    ]
    assert "stage_failed" not in states
    assert states.count(final_state) == 1


def test_low_budget_after_acquire_stops_before_next_stage(message):
    context = Context()
    store = Store(context=context)
    adapter = Adapter()

    with pytest.raises(TimeoutError):
        process(sqs(message), context, store, adapter)

    assert [call[0] for call in store.calls] == ["acquire"]
    assert adapter.calls == []


def test_low_budget_after_generation_does_not_create_slack_adapter(
    message, monkeypatch
):
    context = Context()
    store = Store()
    generated = store.generated

    def exhaust(event, reply, **kwargs):
        result = generated(event, reply, **kwargs)
        context.remaining = 0
        return result

    store.generated = exhaust
    monkeypatch.setattr(
        pipeline,
        "SlackReplyAdapter",
        lambda **kwargs: pytest.fail("Slack adapter must not be created"),
    )

    with pytest.raises(TimeoutError):
        process(sqs(message), context, store)


def test_logs_stage_durations_and_event_id(message, capsys):
    process(sqs(message), Context(), Store(), Adapter())

    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    stages = [record for record in records if "stage" in record]
    assert {record["stage"] for record in stages} >= {
        "validate",
        "acquire",
        "started",
        "generated",
        "send",
    }
    assert all(record["event_id"] == message["event_id"] for record in records)
    assert all(record["correlation_id"] == message["event_id"] for record in records)
    assert all(record["duration_ms"] >= 0 for record in stages)
    completed = [record for record in records if record["state"] == "answer_completed"]
    assert len(completed) == 1 and completed[0]["response_ms"] >= 0


def test_failure_log_has_bounded_fields_and_error_class(message, monkeypatch, capsys):
    class LongNamedFailure(Exception):
        pass

    monkeypatch.setattr(
        pipeline,
        "generate",
        lambda unused: (_ for _ in ()).throw(LongNamedFailure("private detail")),
    )
    message["event_id"] = "E" * 2_000

    with pytest.raises(LongNamedFailure):
        process(sqs(message), Context(), Store(), Adapter())

    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    failed = next(record for record in records if record["state"] == "stage_failed")
    assert failed["error_class"] == "LongNamedFailure"
    assert len(failed["event_id"]) <= 1_000
    assert "private detail" not in json.dumps(failed)


def test_lambda_handler_runs_pipeline(monkeypatch):
    called = []
    monkeypatch.setattr(app, "process", lambda event, context: called.append((event, context)))

    event = {"Records": []}
    context = Context()
    assert app.lambda_handler(event, context) is None
    assert called == [(event, context)]


def test_packaged_handler_imports(tmp_path):
    from pathlib import Path
    import shutil
    import subprocess
    import sys

    source = Path(__file__).resolve().parents[3] / "src" / "worker"
    for path in source.glob("*.py"):
        shutil.copy(path, tmp_path)
    shutil.copytree(source / "tools", tmp_path / "tools")

    result = subprocess.run(
        [sys.executable, "-c", "import app; assert callable(app.lambda_handler)"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
