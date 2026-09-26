import hashlib
import json
import logging
import re
import time

if __package__:
    from .conversation import MAX_ANSWERS_PER_THREAD, generate
    from .discord_reply import DiscordReplyAdapter, PermanentDiscordError
    from .observability import emit
    from .slack_reply import PermanentSlackError, SlackReplyAdapter
    from .store import Conflict, EventExpired, SessionBusy, SessionStopped, Store
else:
    from conversation import MAX_ANSWERS_PER_THREAD, generate
    from discord_reply import DiscordReplyAdapter, PermanentDiscordError
    from observability import emit
    from slack_reply import PermanentSlackError, SlackReplyAdapter
    from store import Conflict, EventExpired, SessionBusy, SessionStopped, Store


MIN_REMAINING_MS = 5_000
MAX_MESSAGE_BYTES = 128 * 1024
ANSWER_LIMIT_NOTICE = "このスレッドは回答の上限に達しました。新しいスレッドを開始してください。"
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
DISCORD_MESSAGE_FIELDS = {
    "schema_version",
    "platform",
    "event_id",
    "guild_id",
    "channel_id",
    "conversation_id",
    "create_thread",
    "user_id",
    "text",
    "received_at",
}


class InvalidMessage(Exception):
    """The SQS record does not satisfy the ingress message contract."""


def _log(level, state, event_id="unknown", **fields):
    emit(
        "worker",
        level,
        state=state,
        event_id=event_id,
        correlation_id=event_id,
        **fields,
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


def _snowflake(value):
    value = _string(value)
    if not value.isascii() or not value.isdigit():
        raise InvalidMessage("Invalid Discord snowflake")
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
        if not isinstance(message, dict):
            raise InvalidMessage("Invalid message fields")
        if type(message["schema_version"]) is not int or message["schema_version"] != 1:
            raise InvalidMessage("Invalid schema version")
        if message.get("platform", "slack") == "discord":
            if not DISCORD_MESSAGE_FIELDS <= message.keys():
                raise InvalidMessage("Invalid Discord message fields")
            for field in (
                "event_id", "guild_id", "channel_id", "conversation_id", "user_id"
            ):
                _snowflake(message[field])
            if type(message["create_thread"]) is not bool:
                raise InvalidMessage("Invalid thread creation flag")
            expected_conversation = (
                message["event_id"] if message["create_thread"] else message["channel_id"]
            )
            if message["conversation_id"] != expected_conversation:
                raise InvalidMessage("Invalid Discord conversation")
            if not isinstance(message["text"], str):
                raise InvalidMessage("Invalid message text")
            if type(message["received_at"]) is not int or message["received_at"] < 0:
                raise InvalidMessage("Invalid receipt time")
            return message
        if message.get("platform", "slack") != "slack" or not MESSAGE_FIELDS <= message.keys():
            raise InvalidMessage("Invalid message fields")
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
    identity = (
        ["discord", message["guild_id"], message["conversation_id"]]
        if message.get("platform", "slack") == "discord"
        else [message["team_id"], message["channel_id"], message["thread_ts"]]
    )
    value = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def _stage(name, event_id, context, operation):
    if context.get_remaining_time_in_millis() < MIN_REMAINING_MS:
        _log(logging.WARNING, "budget_exhausted", event_id, stage=name)
        raise TimeoutError(f"Insufficient time before {name}")
    started = time.monotonic()
    try:
        result = operation()
    except (
        EventExpired,
        PermanentDiscordError,
        PermanentSlackError,
        SessionBusy,
        SessionStopped,
    ):
        raise
    except Exception as error:
        _log(
            logging.WARNING,
            "stage_failed",
            event_id,
            stage=name,
            duration_ms=round((time.monotonic() - started) * 1000, 3),
            error_class=type(error).__name__,
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
    platform = message.get("platform", "slack")
    team_id = message.get("guild_id", message.get("team_id"))
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
                team_id,
                event_id,
                _thread_hash(message),
                context.aws_request_id,
                received_at=message["received_at"],
                remaining_ms=context.get_remaining_time_in_millis(),
                platform=platform,
            ),
        )
    except SessionStopped:
        _log(
            logging.ERROR,
            "session_stopped",
            event_id,
            error_class="SessionStopped",
        )
        raise
    except SessionBusy:
        _log(logging.WARNING, "session_busy", event_id)
        raise
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
        if saved.get("answer_count", 0) >= MAX_ANSWERS_PER_THREAD:
            reply = ANSWER_LIMIT_NOTICE
            answer_limit_notice = True
        else:
            saved = _stage("started", event_id, context, lambda: store.started(saved))
            reply = _stage(
                "conversation", event_id, context, lambda: generate(message)
            )
            answer_limit_notice = False
        saved = _stage(
            "generated",
            event_id,
            context,
            lambda: store.generated(saved, reply, answer_limit_notice=answer_limit_notice),
        )
    elif saved["status"] not in {"GENERATED", "POSTING"}:
        raise Conflict("Unknown event status")
    try:
        if platform == "discord":
            send = lambda: (adapter or DiscordReplyAdapter(store=store)).send(
                saved,
                team_id,
                message["channel_id"],
                message["conversation_id"],
                context,
                source_message_id=message["event_id"],
                create_thread=message["create_thread"],
            )
        else:
            send = lambda: (adapter or SlackReplyAdapter(store=store)).send(
                saved,
                team_id,
                message["channel_id"],
                message["thread_ts"],
                context,
            )
        _stage("send", event_id, context, send)
    except (PermanentDiscordError, PermanentSlackError):
        _log(logging.ERROR, "delivery_isolated", event_id)
        return "isolated"
    _log(
        logging.INFO,
        "answer_completed",
        event_id,
        response_ms=max(0, round((time.time() - message["received_at"]) * 1000, 3)),
    )
    return "completed"
