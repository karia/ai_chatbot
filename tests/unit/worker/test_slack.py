import json
from decimal import Decimal

import pytest
from slack_sdk.errors import SlackApiError, SlackRequestError

from worker.slack import (
    PermanentSlackError,
    RetryableSlackError,
    SlackReplyAdapter,
)


class Response:
    def __init__(self, status, retry_after=None):
        self.status_code = status
        self.headers = {} if retry_after is None else {"Retry-After": str(retry_after)}


class Client:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def _call(self, method, **kwargs):
        self.calls.append((method, kwargs))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def chat_postMessage(self, **kwargs):
        return self._call("post", **kwargs)

    def chat_update(self, **kwargs):
        return self._call("update", **kwargs)


class Store:
    def prepare_posts(self, event, splits):
        event = {**event, "status": "POSTING"}
        event.setdefault("slack_parts", [{"end": end} for end in splits])
        return event

    def start_post(self, event, index, team, channel):
        return {**event, "posting_part": index}

    def posted(self, event, index, ts):
        parts = [dict(part) for part in event["slack_parts"]]
        parts[index]["ts"] = ts
        event = {**event, "slack_parts": parts}
        event.pop("posting_part")
        return event

    def defer_post(self, event, retry_at):
        event = {**event, "status": "GENERATED", "retry_at": retry_at}
        event.pop("posting_part", None)
        self.event = event
        return event

    def complete(self, event, ts):
        self.event = {**event, "status": "COMPLETED", "slack_ts": ts}
        return self.event

    def needs_review(self, event, failure):
        self.event = {**event, "status": "NEEDS_REVIEW", "failure": failure}
        return self.event


class Context:
    def __init__(self, milliseconds):
        self.milliseconds = milliseconds

    def get_remaining_time_in_millis(self):
        return self.milliseconds


def event(reply="answer", **fields):
    return {"status": "GENERATED", "reply": reply, **fields}


def slack_error(status, retry_after=None):
    return SlackApiError("Slack rejected the request", Response(status, retry_after))


def test_post_disconnect_is_not_reposted_and_is_isolated_on_resume():
    store = Store()
    client = Client({"ts": "1.0"}, SlackRequestError("response lost"))
    sleeps = []
    adapter = SlackReplyAdapter(store=store, client=client, sleep=sleeps.append)
    reply = "a" * 80_001

    with pytest.raises(RetryableSlackError):
        adapter.send(event(reply), "T", "C", "root", Context(10_000))

    interrupted = adapter.event
    assert interrupted["slack_parts"] == [
        {"end": 40_000, "ts": "1.0"},
        {"end": 80_000},
        {"end": 80_001},
    ]
    assert interrupted["posting_part"] == 1
    with pytest.raises(PermanentSlackError):
        SlackReplyAdapter(store=store, client=Client()).send(
            interrupted, "T", "C", "root", Context(10_000)
        )
    assert store.event["status"] == "NEEDS_REVIEW"
    assert store.event["failure"] == "slack_post_unknown"
    assert [method for method, _ in client.calls] == ["post", "post"]
    assert sleeps == [1]


def test_resume_updates_known_posts_and_only_posts_missing_parts():
    store = Store()
    client = Client({}, {"ts": "2.0"})
    interrupted = event(
        "abcdefgh",
        status="POSTING",
        slack_parts=[
            {"end": Decimal("4"), "ts": "1.0"},
            {"end": Decimal("8")},
        ],
    )

    SlackReplyAdapter(store=store, client=client).send(
        interrupted, "T", "C", "root", Context(10_000)
    )

    assert client.calls == [
        ("update", {"channel": "C", "ts": "1.0", "text": "abcd"}),
        ("post", {"channel": "C", "thread_ts": "root", "text": "efgh"}),
    ]
    assert store.event["slack_ts"] == "2.0"


def test_429_waits_and_retries_once_when_time_remains():
    store = Store()
    client = Client(slack_error(429, 2), {"ts": "1.0"})
    sleeps = []

    SlackReplyAdapter(
        store=store, client=client, sleep=sleeps.append, now=lambda: 100
    ).send(event(), "T", "C", "root", Context(2_001))

    assert sleeps == [2]
    assert len(client.calls) == 2
    assert store.event["status"] == "COMPLETED"


def test_429_defers_without_waiting_when_time_is_insufficient():
    store = Store()
    client = Client(slack_error(429, 3))
    adapter = SlackReplyAdapter(store=store, client=client, now=lambda: 100)

    with pytest.raises(RetryableSlackError):
        adapter.send(event(), "T", "C", "root", Context(3_000))

    assert store.event["status"] == "GENERATED"
    assert store.event["retry_at"] == 103
    assert len(client.calls) == 1


def test_5xx_is_retryable_without_repost_and_non_429_4xx_is_permanent():
    store = Store()
    adapter = SlackReplyAdapter(
        store=store, client=Client(slack_error(503)), now=lambda: 100
    )
    with pytest.raises(RetryableSlackError):
        adapter.send(event(), "T", "C", "root", Context(10_000))
    assert adapter.event["posting_part"] == 0
    with pytest.raises(PermanentSlackError):
        SlackReplyAdapter(store=store, client=Client()).send(
            adapter.event, "T", "C", "root", Context(10_000)
        )
    assert store.event["failure"] == "slack_post_unknown"

    store = Store()
    with pytest.raises(PermanentSlackError):
        SlackReplyAdapter(store=store, client=Client(slack_error(403))).send(
            event(), "T", "C", "root", Context(10_000)
        )
    assert store.event["status"] == "NEEDS_REVIEW"


def test_logs_truncate_text_and_never_include_the_token(monkeypatch, capsys):
    token = "xoxb-super-secret"
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    client = Client({"ts": "1.0"})
    client.token = token
    SlackReplyAdapter(store=Store(), client=client).send(
        event(token + "x" * 2_000), "T", "C", "root", Context(10_000)
    )

    output = capsys.readouterr().out
    records = [json.loads(line) for line in output.splitlines()]
    assert token not in output
    assert len(records[0]["text"]) == 1_000
    assert records[0]["text_truncated"] is True


def test_secret_token_client_has_no_automatic_retry(monkeypatch):
    created = {}

    class Secrets:
        def get_secret_value(self, **kwargs):
            assert kwargs == {"SecretId": "secret-arn"}
            return {"SecretString": "xoxb-secret"}

    monkeypatch.setenv("BOT_TOKEN_SECRET_ARN", "secret-arn")
    monkeypatch.setattr("worker.slack.boto3.client", lambda service: Secrets())
    monkeypatch.setattr(
        "worker.slack.WebClient", lambda **kwargs: created.update(kwargs) or Client()
    )

    SlackReplyAdapter(store=Store())

    assert created == {"token": "xoxb-secret", "retry_handlers": []}
