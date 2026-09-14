import hashlib
import json
import logging
import os
import re
import time

if __package__:
    from .slack import PermanentSlackError, SlackReplyAdapter
    from .store import Conflict, EventExpired, Store
else:
    from slack import PermanentSlackError, SlackReplyAdapter
    from store import Conflict, EventExpired, Store


FIXED_REPLY = "（応答生成は準備中です）"
MIN_REMAINING_MS = 5_000
MAX_MESSAGE_BYTES = 128 * 1024
MESSAGE_FIELDS = {
    "schema_version",
    "event_id",
    "team_id",
    "api_app_id",
    "channel_id",
    "user_id",
    "thread_ts",
    "message_ts",
    "text",
    "file_ids",
    "received_at",
}


class InvalidMessage(Exception):
    """The SQS record does not satisfy the ingress message contract."""


def _log(level, state, event_id="unknown", **fields):
    threshold = logging.getLevelNamesMapping().get(
        os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO
    )
    if level >= threshold:
        print(
            json.dumps(
                {
                    "level": logging.getLevelName(level),
                    "component": "worker",
                    "state": state,
                    "event_id": event_id[:1024],
                    **fields,
                }
            ),
            flush=True,
        )


def _string(value):
    if not isinstance(value, str) or not value:
        raise InvalidMessage("Expected a nonempty string")
    return value


def _timestamp(value):
    value = _string(value)
    if not re.fullmatch(r"[0-9]+\.[0-9]+", value):
        raise InvalidMessage("Invalid Slack timestamp")
    return value


def validate(event):
    try:
        records = event["Records"]
        if not isinstance(records, list) or len(records) != 1:
            raise InvalidMessage("Expected one SQS record")
        body = records[0]["body"]
        if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise InvalidMessage("Invalid SQS body")
        message = json.loads(body)
        if not isinstance(message, dict) or set(message) != MESSAGE_FIELDS:
            raise InvalidMessage("Invalid message fields")
        if type(message["schema_version"]) is not int or message["schema_version"] != 1:
            raise InvalidMessage("Invalid schema version")
        for field in (
            "event_id",
            "team_id",
            "api_app_id",
            "channel_id",
            "user_id",
        ):
            _string(message[field])
        _timestamp(message["thread_ts"])
        _timestamp(message["message_ts"])
        if not isinstance(message["text"], str):
            raise InvalidMessage("Invalid message text")
        if not isinstance(message["file_ids"], list):
            raise InvalidMessage("Invalid file IDs")
        for file_id in message["file_ids"]:
            _string(file_id)
        if type(message["received_at"]) is not int or message["received_at"] < 0:
            raise InvalidMessage("Invalid receipt time")
        return message
    except (KeyError, TypeError, UnicodeError, RecursionError, json.JSONDecodeError) as error:
        raise InvalidMessage("Invalid SQS record") from error


def _thread_hash(message):
    value = json.dumps(
        [message["team_id"], message["channel_id"], message["thread_ts"]],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(value.encode()).hexdigest()


def _stage(name, event_id, context, operation):
    if context.get_remaining_time_in_millis() < MIN_REMAINING_MS:
        _log(logging.WARNING, "budget_exhausted", event_id, stage=name)
        raise TimeoutError(f"Insufficient time before {name}")
    started = time.monotonic()
    try:
        result = operation()
    except Exception as error:
        _log(
            logging.WARNING,
            "stage_failed",
            event_id,
            stage=name,
            duration_ms=round((time.monotonic() - started) * 1000, 3),
            error=type(error).__name__,
        )
        raise
    _log(
        logging.INFO,
        "stage_completed",
        event_id,
        stage=name,
        duration_ms=round((time.monotonic() - started) * 1000, 3),
        status=result.get("status") if isinstance(result, dict) else None,
        remaining_ms=context.get_remaining_time_in_millis(),
    )
    return result


def process(event, context, store=None, adapter=None):
    started = time.monotonic()
    try:
        message = validate(event)
    except InvalidMessage:
        _log(
            logging.ERROR,
            "message_isolated",
            duration_ms=round((time.monotonic() - started) * 1000, 3),
            stage="validate",
        )
        return "isolated"
    event_id = message["event_id"]
    _log(
        logging.INFO,
        "stage_completed",
        event_id,
        stage="validate",
        duration_ms=round((time.monotonic() - started) * 1000, 3),
    )
    store = store or Store()
    try:
        saved = _stage(
            "acquire",
            event_id,
            context,
            lambda: store.acquire(
                message["team_id"],
                event_id,
                _thread_hash(message),
                context.aws_request_id,
                received_at=message["received_at"],
            ),
        )
    except EventExpired:
        _log(logging.ERROR, "event_expired", event_id)
        return "isolated"
    if saved["status"] == "COMPLETED":
        _log(logging.INFO, "duplicate_completed", event_id)
        return "duplicate"
    if saved["status"] == "RUNNING":
        if saved.get("phase") == "EXECUTING":
            _stage(
                "needs_review",
                event_id,
                context,
                lambda: store.needs_review(saved, "execution_unknown"),
            )
            return "isolated"
        if "phase" in saved:
            raise Conflict("Unknown execution phase")
        saved = _stage("started", event_id, context, lambda: store.started(saved))
        saved = _stage(
            "generated",
            event_id,
            context,
            lambda: store.generated(saved, FIXED_REPLY),
        )
    elif saved["status"] not in {"GENERATED", "POSTING"}:
        raise Conflict("Unknown event status")
    try:
        _stage(
            "send",
            event_id,
            context,
            lambda: (adapter or SlackReplyAdapter(store=store)).send(
                saved,
                message["team_id"],
                message["channel_id"],
                message["thread_ts"],
                context,
            ),
        )
    except PermanentSlackError:
        _log(logging.ERROR, "delivery_isolated", event_id)
        return "isolated"
    return "completed"
