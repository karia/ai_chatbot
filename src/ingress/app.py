import base64
import json
import logging
import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

if __package__:
    from .events import fifo_id, normalize
    from .signature import verify
else:
    from events import fifo_id, normalize
    from signature import verify


MAX_MESSAGE_BYTES = 128 * 1024

_clients = {}
_secret = None


def _client(service):
    if service not in _clients:
        _clients[service] = boto3.client(
            service,
            config=Config(
                connect_timeout=0.3,
                read_timeout=0.5,
                retries={"mode": "standard", "total_max_attempts": 1},
            ),
        )
    return _clients[service]


def _signing_secret(now):
    global _secret
    reference = os.environ["SIGNING_SECRET_ARN"]
    if _secret is None or _secret[0] != reference or now >= _secret[2]:
        value = _client("secretsmanager").get_secret_value(SecretId=reference).get("SecretString")
        if not isinstance(value, str) or not value:
            raise ValueError("Invalid signing secret")
        _secret = (reference, value, now + 300)
    return _secret[1]


def _respond(status, state, started, *, body="", level="INFO", **fields):
    levels = logging.getLevelNamesMapping()
    if levels[level] >= levels.get(os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO):
        for key, value in fields.items():
            if isinstance(value, str) and len(value) > 1024:
                fields[key] = value[:1013] + "[truncated]"
        print(json.dumps({
            "level": level,
            "component": "ingress",
            "state": state,
            "status_code": status,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            **fields,
        }))
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": body}


def lambda_handler(event, context):
    started = time.monotonic()
    now = int(time.time())
    try:
        body = event.get("body", "")
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body, validate=True)
        else:
            body = body.encode("utf-8")
        headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
    except (ValueError, TypeError, AttributeError):
        return _respond(400, "invalid_payload", started, level="WARN")
    try:
        secret = _signing_secret(now)
    except KeyError:
        return _respond(503, "invalid_configuration", started, level="ERROR")
    except (BotoCoreError, ClientError, ValueError):
        return _respond(503, "secret_unavailable", started, level="ERROR")
    if not verify(body, headers, secret, now):
        return _respond(401, "invalid_signature", started, level="WARN")
    try:
        payload = json.loads(body)
        if isinstance(payload, dict) and payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str) or not challenge:
                raise ValueError("Invalid challenge")
            return _respond(200, "verified", started, body=json.dumps({"challenge": challenge}))
        team_id = os.environ["SLACK_TEAM_ID"]
        api_app_id = os.environ["SLACK_API_APP_ID"]
        if not team_id or not api_app_id:
            raise KeyError("Missing Slack configuration")
        message = normalize(payload, team_id, api_app_id, now)
        if message is None:
            return _respond(200, "ignored", started)
        message_body = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        message_bytes = len(message_body.encode("utf-8"))
    except (ValueError, RecursionError):
        return _respond(400, "invalid_payload", started, level="WARN")
    except KeyError:
        return _respond(503, "invalid_configuration", started, level="ERROR")
    fields = {"event_id": message["event_id"], "message_bytes": message_bytes}
    if message_bytes > MAX_MESSAGE_BYTES:
        return _respond(413, "oversized", started, level="WARN", oversized_count=1, **fields)
    try:
        result = _client("sqs").send_message(
            QueueUrl=os.environ["QUEUE_URL"],
            MessageBody=message_body,
            MessageGroupId=fifo_id(message["team_id"], message["channel_id"], message["thread_ts"]),
            MessageDeduplicationId=fifo_id(message["team_id"], message["event_id"]),
        )
        if not result or not result.get("MessageId"):
            return _respond(503, "queue_failed", started, level="ERROR", **fields)
    except KeyError:
        return _respond(503, "invalid_configuration", started, level="ERROR", **fields)
    except (BotoCoreError, ClientError):
        return _respond(503, "queue_failed", started, level="ERROR", **fields)
    return _respond(200, "queued", started, **fields)
