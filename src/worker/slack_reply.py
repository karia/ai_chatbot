"""Slack thread reply delivery with persisted post boundaries."""

import logging
import math
import os
import time

import boto3
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError, SlackRequestError

if __package__:
    from .observability import emit
    from .store import PostingSlotUnavailable, Store
else:
    from observability import emit
    from store import PostingSlotUnavailable, Store


MESSAGE_LIMIT = 12_000
LOG_FIELD_LIMIT = 1_000
DEFAULT_RETRY_AFTER = 3
MIN_REMAINING_MS = 5_000


class RetryableSlackError(Exception):
    """Slack delivery can be retried after the persisted state permits it."""


class PermanentSlackError(Exception):
    """Slack delivery was isolated and must not be retried automatically."""


def _log(level, operation, *, secret_values=(), **fields):
    fields.setdefault("correlation_id", "unknown")
    emit(
        "slack_reply",
        level,
        secret_values=secret_values,
        state=operation,
        operation=operation,
        **fields,
    )


def _retry_after(headers):
    value = {key.lower(): value for key, value in headers.items()}.get(
        "retry-after", DEFAULT_RETRY_AFTER
    )
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER
    return seconds if math.isfinite(seconds) and seconds >= 0 else DEFAULT_RETRY_AFTER


class SlackReplyAdapter:
    def __init__(self, store=None, client=None, *, sleep=time.sleep, now=time.time):
        self.store = store or Store()
        if client is None:
            secret = boto3.client("secretsmanager").get_secret_value(
                SecretId=os.environ["BOT_TOKEN_SECRET_ARN"]
            )["SecretString"]
            client = WebClient(token=secret, retry_handlers=[])
        self.client = client
        self.sleep = sleep
        self.now = now
        self.event = None

    def _log_text(self, operation, text, **fields):
        token = getattr(self.client, "token", None)
        if token:
            text = text.replace(token, "[REDACTED]")
        _log(
            logging.INFO,
            operation,
            secret_values=(token,),
            text=text,
            text_truncated=len(text) > LOG_FIELD_LIMIT,
            **fields,
        )

    def _call(self, method, event, context, **kwargs):
        correlation_id = event.get("pk", "unknown").rsplit("#", 1)[-1]
        for attempt in range(2):
            try:
                return method(**kwargs)
            except SlackApiError as error:
                status = error.response.status_code
                if status == 429:
                    retry_after = _retry_after(error.response.headers)
                    if (
                        attempt == 0
                        and retry_after * 1_000 + MIN_REMAINING_MS
                        < context.get_remaining_time_in_millis()
                    ):
                        _log(
                            logging.WARNING,
                            "slack_rate_limited",
                            retry_after=retry_after,
                            error_class=type(error).__name__,
                            correlation_id=correlation_id,
                        )
                        self.sleep(retry_after)
                        continue
                    self.event = self.store.defer_post(
                        event, self.now() + retry_after
                    )
                    raise RetryableSlackError("Slack rate limited the request") from error
                if status >= 500:
                    _log(
                        logging.WARNING,
                        "slack_retryable_failure",
                        status=status,
                        error_class=type(error).__name__,
                        correlation_id=correlation_id,
                    )
                    raise RetryableSlackError("Slack failed temporarily") from error
                self.event = self.store.needs_review(event, "slack_permanent")
                raise PermanentSlackError("Slack rejected the post") from error
            except SlackRequestError as error:
                _log(
                    logging.WARNING,
                    "slack_response_unavailable",
                    error_class=type(error).__name__,
                    correlation_id=correlation_id,
                )
                raise RetryableSlackError("Slack response was unavailable") from error
        raise AssertionError("Slack retry loop exhausted")

    def send(self, event, team, channel, thread_ts, context):
        if not event["reply"].strip():
            self.event = self.store.needs_review(event, "slack_empty_reply")
            raise PermanentSlackError("Slack reply is empty")
        splits = list(range(MESSAGE_LIMIT, len(event["reply"]), MESSAGE_LIMIT)) + [
            len(event["reply"])
        ]
        if event["status"] == "GENERATED":
            event = self.store.prepare_posts(event, splits)
        if "posting_part" in event:
            self.event = self.store.needs_review(event, "slack_post_unknown")
            raise PermanentSlackError("Slack post result is unknown")

        start = 0
        correlation_id = event.get("pk", "unknown").rsplit("#", 1)[-1]
        for index, part in enumerate(event["slack_parts"]):
            end = int(part["end"])
            text = event["reply"][start:end]
            start = end
            if "ts" in part:
                self.event = event
                self._log_text("slack_update", text, part=index)
                self._call(
                    self.client.chat_update,
                    event,
                    context,
                    channel=channel,
                    ts=part["ts"],
                    markdown_text=text,
                )
                continue
            for attempt in range(10):
                try:
                    event = self.store.start_post(event, index, team, channel)
                    break
                except PostingSlotUnavailable as error:
                    wait = max(0, float(error.next_post_at) - self.now())
                    if (
                        attempt < 9
                        and wait * 1_000 + MIN_REMAINING_MS
                        < context.get_remaining_time_in_millis()
                    ):
                        _log(
                            logging.WARNING,
                            "posting_slot_wait",
                            attempt=attempt + 1,
                            wait=wait,
                            correlation_id=correlation_id,
                        )
                        self.sleep(wait)
                        continue
                    raise
            self.event = event
            self._log_text(
                "slack_post", text, part=index, correlation_id=correlation_id
            )
            response = self._call(
                self.client.chat_postMessage,
                event,
                context,
                channel=channel,
                thread_ts=thread_ts,
                markdown_text=text,
            )
            event = self.store.posted(event, index, response["ts"])
            self.event = event
            if index + 1 < len(event["slack_parts"]):
                self.sleep(1)

        self.event = self.store.complete(event, event["slack_parts"][-1]["ts"])
        return self.event
