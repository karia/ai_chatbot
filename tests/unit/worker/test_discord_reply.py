import io
import json
from urllib.error import HTTPError, URLError

import pytest

from worker.discord_reply import (
    DiscordReplyAdapter,
    PermanentDiscordError,
    RetryableDiscordError,
)


class Context:
    def __init__(self, remaining=300_000):
        self.remaining = remaining

    def get_remaining_time_in_millis(self):
        return self.remaining


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self.payload


class Store:
    def __init__(self):
        self.calls = []

    def prepare_posts(self, event, splits):
        self.calls.append(("prepare_posts", splits))
        return {**event, "status": "POSTING", "slack_parts": [{"end": end} for end in splits]}

    def start_post(self, event, index, team, channel, *, platform="slack"):
        self.calls.append(("start_post", index, team, channel, platform))
        return {**event, "posting_part": index}

    def posted(self, event, index, message_id):
        parts = [dict(part) for part in event["slack_parts"]]
        parts[index]["ts"] = message_id
        result = {**event, "slack_parts": parts}
        result.pop("posting_part")
        self.calls.append(("posted", index, message_id))
        return result

    def complete(self, event, message_id):
        self.calls.append(("complete", message_id))
        return {**event, "status": "COMPLETED"}

    def defer_post(self, event, retry_at):
        self.calls.append(("defer_post", retry_at))
        return {**event, "status": "GENERATED", "retry_at": retry_at}

    def needs_review(self, event, failure):
        self.calls.append(("needs_review", failure))
        return {**event, "status": "NEEDS_REVIEW"}


def event(reply="answer"):
    return {"pk": "EVENT#discord#G#M", "status": "GENERATED", "reply": reply}


def http_error(status, payload):
    return HTTPError(
        "https://discord.com/api/v10/test", status, "error", {},
        io.BytesIO(json.dumps(payload).encode()),
    )


def next_response(responses):
    response = next(responses)
    if isinstance(response, Exception):
        raise response
    return response


def test_creates_thread_splits_replies_and_suppresses_mentions():
    requests = []
    responses = iter([Response({"id": "M"}), Response({"id": "R1"}), Response({"id": "R2"})])

    def open_request(request, timeout):
        requests.append((request, timeout))
        return next(responses)

    store = Store()
    adapter = DiscordReplyAdapter(store=store, token="secret", open_request=open_request, sleep=lambda _: None)
    adapter.send(
        event("x" * 2001), "G", "C", "M", Context(),
        source_message_id="M", create_thread=True,
    )

    assert [request.method for request, _ in requests] == ["POST", "POST", "POST"]
    assert requests[0][0].full_url.endswith("/channels/C/messages/M/threads")
    assert requests[1][0].full_url.endswith("/channels/M/messages")
    assert requests[1][0].get_header("User-agent") == (
        "DiscordBot (https://github.com/karia/ai_chatbot, 1.0)"
    )
    payloads = [json.loads(request.data) for request, _ in requests]
    assert len(payloads[1]["content"]) == 2000
    assert payloads[2]["content"] == "x"
    assert payloads[1]["allowed_mentions"] == {"parse": [], "replied_user": False}
    assert ("start_post", 0, "G", "M", "discord") in store.calls
    assert store.calls[-1] == ("complete", "R2")


def test_existing_saved_part_is_updated_on_retry():
    requests = []
    saved = {
        **event(),
        "status": "POSTING",
        "slack_parts": [{"end": 6, "ts": "R1"}],
    }
    adapter = DiscordReplyAdapter(
        store=Store(), token="secret",
        open_request=lambda request, timeout: requests.append(request) or Response({"id": "R1"}),
    )

    adapter.send(saved, "G", "M", "M", Context(), source_message_id="N", create_thread=False)

    assert requests[0].method == "PATCH"
    assert requests[0].full_url.endswith("/channels/M/messages/R1")


def test_thread_already_exists_is_tolerated():
    responses = iter([
        http_error(400, {"code": 160004, "message": "Thread already exists"}),
        Response({"id": "R1"}),
    ])
    adapter = DiscordReplyAdapter(
        store=Store(), token="secret",
        open_request=lambda request, timeout: next_response(responses),
    )

    assert adapter.send(
        event(), "G", "C", "M", Context(), source_message_id="M", create_thread=True,
    )["status"] == "COMPLETED"


@pytest.mark.parametrize("global_limit,operation", [(False, "discord_rate_limited"), (True, "discord_global_rate_limited")])
def test_429_retries_once_and_distinguishes_global_limit(global_limit, operation, capsys):
    responses = iter([
        http_error(429, {"retry_after": 0.25, "global": global_limit}),
        Response({"id": "R1"}),
    ])
    sleeps = []
    adapter = DiscordReplyAdapter(
        store=Store(), token="secret",
        open_request=lambda request, timeout: next_response(responses),
        sleep=sleeps.append,
    )

    adapter.send(event(), "G", "M", "M", Context(), source_message_id="N", create_thread=False)

    assert sleeps == [0.25]
    assert operation in capsys.readouterr().out


def test_429_is_deferred_when_lambda_budget_is_low():
    store = Store()
    adapter = DiscordReplyAdapter(
        store=store, token="secret",
        open_request=lambda request, timeout: (_ for _ in ()).throw(
            http_error(429, {"retry_after": 20, "global": False})
        ),
        now=lambda: 1000,
    )

    with pytest.raises(RetryableDiscordError):
        adapter.send(
            event(), "G", "M", "M", Context(15_000),
            source_message_id="N", create_thread=False,
        )

    assert store.calls[-1] == ("defer_post", 1020)


@pytest.mark.parametrize(
    "saved",
    [
        event(),
        {
            **event(),
            "status": "POSTING",
            "slack_parts": [{"end": 6, "ts": "R1"}],
        },
    ],
)
def test_low_budget_defers_before_reserving_or_sending_part(saved):
    store = Store()
    adapter = DiscordReplyAdapter(
        store=store,
        token="secret",
        open_request=lambda request, timeout: pytest.fail("Discord request sent"),
        now=lambda: 1000,
    )

    with pytest.raises(RetryableDiscordError):
        adapter.send(
            saved,
            "G",
            "M",
            "M",
            Context(14_999),
            source_message_id="N",
            create_thread=False,
        )

    assert not any(call[0] == "start_post" for call in store.calls)
    assert store.calls[-1] == ("defer_post", 1000)


@pytest.mark.parametrize("error", [http_error(503, {}), URLError("disconnected")])
def test_temporary_failures_are_retried_by_sqs(error):
    adapter = DiscordReplyAdapter(
        store=Store(), token="secret",
        open_request=lambda request, timeout: (_ for _ in ()).throw(error),
    )

    with pytest.raises(RetryableDiscordError):
        adapter.send(event(), "G", "M", "M", Context(), source_message_id="N", create_thread=False)


def test_permanent_failure_is_isolated():
    store = Store()
    adapter = DiscordReplyAdapter(
        store=store, token="secret",
        open_request=lambda request, timeout: (_ for _ in ()).throw(http_error(403, {"code": 50001})),
    )

    with pytest.raises(PermanentDiscordError):
        adapter.send(event(), "G", "M", "M", Context(), source_message_id="N", create_thread=False)

    assert store.calls[-1] == ("needs_review", "discord_permanent")


def test_reads_bot_token_from_secrets_manager(monkeypatch):
    calls = []
    monkeypatch.setenv("DISCORD_BOT_TOKEN_SECRET_ARN", "secret-arn")
    monkeypatch.setattr(
        "worker.discord_reply.boto3.client",
        lambda service: type("Secrets", (), {
            "get_secret_value": lambda self, **kwargs: calls.append((service, kwargs)) or {"SecretString": "token"}
        })(),
    )

    adapter = DiscordReplyAdapter(store=Store(), open_request=lambda request, timeout: Response({}))

    assert adapter.token == "token"
    assert calls == [("secretsmanager", {"SecretId": "secret-arn"})]
