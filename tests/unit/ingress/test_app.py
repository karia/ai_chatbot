import base64
import hashlib
import hmac
import json
from unittest.mock import Mock

import pytest

from ingress import app


NOW = 1800000000
SECRET = "test-signing-value"


def request(payload, *, timestamp=NOW, encoded=False):
    body = json.dumps(payload, ensure_ascii=False)
    signature = hmac.new(
        SECRET.encode(), f"v0:{timestamp}:{body}".encode(), hashlib.sha256
    ).hexdigest()
    return {
        "version": "2.0",
        "headers": {
            "X-Slack-Signature": f"v0={signature}",
            "X-Slack-Request-Timestamp": str(timestamp),
        },
        "body": base64.b64encode(body.encode()).decode() if encoded else body,
        "isBase64Encoded": encoded,
    }


@pytest.fixture
def payload():
    return {
        "type": "event_callback", "team_id": "TTEST", "api_app_id": "ATEST",
        "event_id": "EvTEST",
        "event": {
            "type": "app_mention", "user": "UTEST", "channel": "CTEST",
            "ts": "1800000000.000001", "text": "こんにちは",
            "files": [{"id": "FTEST", "url_private": "https://example.com/private"}],
        },
    }


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("SLACK_TEAM_ID", "TTEST")
    monkeypatch.setenv("SLACK_API_APP_ID", "ATEST")
    monkeypatch.setenv("SIGNING_SECRET_ARN", "test-signing-reference")
    monkeypatch.setenv("QUEUE_URL", "https://example.com/events.fifo")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setattr(app.time, "time", lambda: NOW)
    client = Mock()
    client.get_secret_value.return_value = {"SecretString": SECRET}
    client.send_message.return_value = {"MessageId": "message-test"}
    monkeypatch.setattr(app, "_clients", {})
    monkeypatch.setattr(app, "_secret", None)
    monkeypatch.setattr(app.boto3, "client", Mock(return_value=client))
    return client


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("lowercase", [False, True])
def test_challenge(aws, encoded, lowercase):
    event = request({"type": "url_verification", "challenge": "challenge-test"}, encoded=encoded)
    if lowercase:
        event["headers"] = {key.lower(): value for key, value in event["headers"].items()}
    result = app.lambda_handler(event, None)
    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"challenge": "challenge-test"}
    aws.send_message.assert_not_called()


@pytest.mark.parametrize("offset,status", [(-301, 401), (301, 401), (-300, 200), (300, 200)])
def test_timestamp_window(aws, payload, offset, status):
    assert app.lambda_handler(request(payload, timestamp=NOW + offset), None)["statusCode"] == status


@pytest.mark.parametrize("mutation", ["tampered", "missing", "invalid_timestamp", "invalid_signature"])
def test_rejects_bad_signature_before_json(aws, mutation):
    event = request({"type": "url_verification", "challenge": "ok"})
    if mutation == "tampered":
        event["body"] = "invalid json"
    elif mutation == "missing":
        event["headers"] = {}
    elif mutation == "invalid_timestamp":
        event["headers"]["X-Slack-Request-Timestamp"] = "invalid"
    else:
        event["headers"]["X-Slack-Signature"] = "v0=invalid"
    assert app.lambda_handler(event, None)["statusCode"] == 401
    aws.send_message.assert_not_called()


def test_normalized_message_and_fifo_ids(aws, payload):
    assert app.lambda_handler(request(payload, encoded=True), None)["statusCode"] == 200
    args = aws.send_message.call_args.kwargs
    assert args["QueueUrl"] == "https://example.com/events.fifo"
    assert json.loads(args["MessageBody"]) == {
        "schema_version": 1, "event_id": "EvTEST", "team_id": "TTEST",
        "api_app_id": "ATEST", "channel_id": "CTEST", "user_id": "UTEST",
        "thread_ts": "1800000000.000001", "message_ts": "1800000000.000001",
        "text": "こんにちは", "file_ids": ["FTEST"], "received_at": NOW,
    }
    assert args["MessageGroupId"] == hashlib.sha256(b'["TTEST","CTEST","1800000000.000001"]').hexdigest()
    assert args["MessageDeduplicationId"] == hashlib.sha256(b'["TTEST","EvTEST"]').hexdigest()


@pytest.mark.parametrize("field,value", [("team_id", "TOTHER"), ("api_app_id", "AOTHER"), ("type", "other")])
def test_ignores_other_envelopes(aws, payload, field, value):
    payload[field] = value
    assert app.lambda_handler(request(payload), None)["statusCode"] == 200
    aws.send_message.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"type": "message"}, {"subtype": "message_changed"}, {"subtype": "bot_message"},
    {"bot_id": "BTEST"}, {"bot_profile": {}}, {"subtype": "message_deleted"},
])
def test_ignores_nonhuman_mentions(aws, payload, changes):
    payload["event"].update(changes)
    assert app.lambda_handler(request(payload), None)["statusCode"] == 200
    aws.send_message.assert_not_called()


@pytest.mark.parametrize("result", [None, {}, {"MessageId": ""}])
def test_unknown_send_result_is_not_acknowledged(aws, payload, result):
    aws.send_message.return_value = result
    assert app.lambda_handler(request(payload), None)["statusCode"] == 503


def test_send_failure_is_not_acknowledged(aws, payload, capsys):
    from botocore.exceptions import EndpointConnectionError
    aws.send_message.side_effect = EndpointConnectionError(endpoint_url="https://example.com")
    assert app.lambda_handler(request(payload), None)["statusCode"] == 503
    assert json.loads(capsys.readouterr().out)["state"] == "queue_failed"


@pytest.mark.parametrize("payload", [[], None, {}, {"type": "url_verification"}, {"type": "url_verification", "challenge": 1}])
def test_invalid_payload(aws, payload):
    assert app.lambda_handler(request(payload), None)["statusCode"] == 400
    aws.send_message.assert_not_called()


@pytest.mark.parametrize("field,value", [("user", None), ("ts", 1.0), ("channel", ""), ("text", None), ("files", [{}]), ("thread_ts", 1)])
def test_invalid_message_contract(aws, payload, field, value):
    payload["event"][field] = value
    assert app.lambda_handler(request(payload), None)["statusCode"] == 400
    aws.send_message.assert_not_called()


def test_invalid_base64(aws):
    assert app.lambda_handler({"body": "%%%", "isBase64Encoded": True}, None)["statusCode"] == 400


def test_thread_ordering_and_retries(aws, payload):
    payload["event"]["thread_ts"] = "1799999999.000001"
    event = request(payload)
    app.lambda_handler(event, None)
    first = aws.send_message.call_args.kwargs
    event["headers"]["X-Slack-Retry-Num"] = "1"
    app.lambda_handler(event, None)
    assert aws.send_message.call_args.kwargs == first
    payload["event_id"] = "EvSECOND"
    payload["event"]["ts"] = "1800000000.000002"
    app.lambda_handler(request(payload), None)
    second = aws.send_message.call_args.kwargs
    assert second["MessageGroupId"] == first["MessageGroupId"]
    assert second["MessageDeduplicationId"] != first["MessageDeduplicationId"]
    payload["event"]["thread_ts"] = "1799999999.000002"
    app.lambda_handler(request(payload), None)
    assert aws.send_message.call_args.kwargs["MessageGroupId"] != first["MessageGroupId"]


def test_message_size_boundary_and_metric(aws, payload, capsys):
    payload["event"]["text"] = ""
    app.lambda_handler(request(payload), None)
    overhead = len(aws.send_message.call_args.kwargs["MessageBody"].encode())
    payload["event"]["text"] = "あ" * ((128 * 1024 - overhead) // 3)
    payload["event"]["text"] += "x" * ((128 * 1024 - overhead) % 3)
    assert app.lambda_handler(request(payload), None)["statusCode"] == 200
    assert len(aws.send_message.call_args.kwargs["MessageBody"].encode()) == 128 * 1024
    aws.send_message.reset_mock()
    payload["event"]["text"] += "x"
    assert app.lambda_handler(request(payload), None)["statusCode"] == 413
    aws.send_message.assert_not_called()
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[-1]["oversized_count"] == 1
    assert records[-1]["message_bytes"] == 128 * 1024 + 1
    assert records[-1]["level"] == "WARN"


def test_secret_cache_expires(aws, payload, monkeypatch):
    app.lambda_handler(request(payload), None)
    app.lambda_handler(request(payload), None)
    aws.get_secret_value.assert_called_once_with(SecretId="test-signing-reference")
    monkeypatch.setattr(app.time, "time", lambda: NOW + 300)
    app.lambda_handler(request(payload, timestamp=NOW + 300), None)
    assert aws.get_secret_value.call_count == 2


def test_secret_failure_is_retryable_and_not_logged(aws, payload, capsys):
    from botocore.exceptions import ClientError
    aws.get_secret_value.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": SECRET}}, "GetSecretValue"
    )
    assert app.lambda_handler(request(payload), None)["statusCode"] == 503
    aws.send_message.assert_not_called()
    output = capsys.readouterr().out
    assert SECRET not in output
    assert json.loads(output)["state"] == "secret_unavailable"


@pytest.mark.parametrize("level,logged", [("DEBUG", True), ("INFO", True), ("WARN", False), ("ERROR", False)])
def test_structured_logging_and_level(aws, payload, monkeypatch, capsys, level, logged):
    monkeypatch.setenv("LOG_LEVEL", level)
    event = request(payload)
    app.lambda_handler(event, None)
    output = capsys.readouterr().out
    assert bool(output) == logged
    assert SECRET not in output
    assert event["headers"]["X-Slack-Signature"] not in output
    if logged:
        record = json.loads(output)
        assert record["state"] == "queued"
        assert record["event_id"] == "EvTEST"
        assert record["duration_ms"] >= 0


def test_sdk_timeouts_and_retry_limit(aws, payload):
    app.lambda_handler(request(payload), None)
    for call in app.boto3.client.call_args_list:
        config = call.kwargs["config"]
        assert config.connect_timeout == 0.3
        assert config.read_timeout == 0.5
        assert config.retries["total_max_attempts"] == 1


def test_log_fields_are_bounded(aws, payload, capsys):
    payload["event_id"] = "E" * 2000
    app.lambda_handler(request(payload), None)
    record = json.loads(capsys.readouterr().out)
    assert len(record["event_id"]) <= 1024
    assert record["event_id"].endswith("[truncated]")


def test_signed_invalid_json(aws):
    body = b'{invalid'
    signature = hmac.new(SECRET.encode(), b"v0:" + str(NOW).encode() + b":" + body, hashlib.sha256).hexdigest()
    event = {
        "body": body.decode(),
        "headers": {"x-slack-signature": "v0=" + signature, "x-slack-request-timestamp": str(NOW)},
    }
    assert app.lambda_handler(event, None)["statusCode"] == 400
    aws.send_message.assert_not_called()


@pytest.mark.parametrize("field", ["SLACK_TEAM_ID", "SLACK_API_APP_ID", "QUEUE_URL", "SIGNING_SECRET_ARN"])
def test_missing_configuration_fails_closed(aws, payload, monkeypatch, field, capsys):
    monkeypatch.delenv(field)
    assert app.lambda_handler(request(payload), None)["statusCode"] == 503
    aws.send_message.assert_not_called()
    assert json.loads(capsys.readouterr().out)["state"] == "invalid_configuration"


def test_packaged_handler_imports(tmp_path):
    from pathlib import Path
    import shutil
    import subprocess
    import sys
    source = Path(__file__).resolve().parents[3] / "src" / "ingress"
    for path in source.glob("*.py"):
        shutil.copy(path, tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", "import app; assert callable(app.lambda_handler)"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
