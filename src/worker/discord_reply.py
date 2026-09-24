import json
import logging
import math
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import boto3

if __package__:
    from .observability import emit
    from .store import PostingSlotUnavailable, Store
else:
    from observability import emit
    from store import PostingSlotUnavailable, Store


API_URL = "https://discord.com/api/v10"
MESSAGE_LIMIT = 2_000
MIN_REMAINING_MS = 5_000
DEFAULT_RETRY_AFTER = 3


class RetryableDiscordError(Exception):
    pass


class PermanentDiscordError(Exception):
    pass


def _log(level, operation, **fields):
    fields.setdefault("correlation_id", "unknown")
    emit(
        "discord_reply",
        level,
        state=operation,
        operation=operation,
        **fields,
    )


class DiscordReplyAdapter:
    def __init__(
        self, store=None, token=None, *, open_request=urlopen, sleep=time.sleep,
        now=time.time,
    ):
        self.store = store or Store()
        self.token = token or boto3.client("secretsmanager").get_secret_value(
            SecretId=os.environ["DISCORD_BOT_TOKEN_SECRET_ARN"]
        )["SecretString"]
        self.open_request = open_request
        self.sleep = sleep
        self.now = now
        self.event = None

    def _request(self, method, path, payload, event, context, *, thread=False):
        correlation_id = event.get("pk", "unknown").rsplit("#", 1)[-1]
        data = json.dumps(payload, ensure_ascii=False).encode()
        for attempt in range(2):
            request = Request(
                f"{API_URL}{path}",
                data=data,
                method=method,
                headers={
                    "Authorization": f"Bot {self.token}",
                    "Content-Type": "application/json",
                    "User-Agent": "ai-chatbot",
                },
            )
            try:
                with self.open_request(request, timeout=10) as response:
                    body = response.read()
                return json.loads(body) if body else {}
            except HTTPError as error:
                try:
                    body = json.loads(error.read())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    body = {}
                if thread and error.code == 400 and body.get("code") == 160004:
                    return {}
                if error.code == 429:
                    retry_after = body.get("retry_after", DEFAULT_RETRY_AFTER)
                    if not isinstance(retry_after, (int, float)) or not math.isfinite(retry_after) or retry_after < 0:
                        retry_after = DEFAULT_RETRY_AFTER
                    operation = (
                        "discord_global_rate_limited"
                        if body.get("global") is True
                        else "discord_rate_limited"
                    )
                    _log(
                        logging.WARNING,
                        operation,
                        retry_after=retry_after,
                        correlation_id=correlation_id,
                    )
                    if (
                        attempt == 0
                        and retry_after * 1_000 + MIN_REMAINING_MS
                        < context.get_remaining_time_in_millis()
                    ):
                        self.sleep(retry_after)
                        continue
                    self.event = self.store.defer_post(event, self.now() + retry_after)
                    raise RetryableDiscordError("Discord rate limited the request") from error
                if error.code >= 500:
                    raise RetryableDiscordError("Discord failed temporarily") from error
                self.event = self.store.needs_review(event, "discord_permanent")
                raise PermanentDiscordError("Discord rejected the request") from error
            except URLError as error:
                raise RetryableDiscordError("Discord response was unavailable") from error
        raise AssertionError("Discord retry loop exhausted")

    def send(
        self, event, guild, channel, conversation, context, *, source_message_id,
        create_thread,
    ):
        if not event["reply"].strip():
            self.event = self.store.needs_review(event, "discord_empty_reply")
            raise PermanentDiscordError("Discord reply is empty")
        splits = list(range(MESSAGE_LIMIT, len(event["reply"]), MESSAGE_LIMIT)) + [
            len(event["reply"])
        ]
        if event["status"] == "GENERATED":
            event = self.store.prepare_posts(event, splits)
        if "posting_part" in event:
            self.event = self.store.needs_review(event, "discord_post_unknown")
            raise PermanentDiscordError("Discord post result is unknown")
        if create_thread:
            self._request(
                "POST",
                f"/channels/{channel}/messages/{source_message_id}/threads",
                {"name": "AI chatbot"},
                event,
                context,
                thread=True,
            )

        start = 0
        for index, part in enumerate(event["slack_parts"]):
            end = int(part["end"])
            payload = {
                "content": event["reply"][start:end],
                "allowed_mentions": {"parse": [], "replied_user": False},
            }
            start = end
            if "ts" in part:
                self._request(
                    "PATCH",
                    f"/channels/{conversation}/messages/{part['ts']}",
                    payload,
                    event,
                    context,
                )
                continue
            for attempt in range(10):
                try:
                    event = self.store.start_post(
                        event, index, guild, conversation, platform="discord"
                    )
                    break
                except PostingSlotUnavailable as error:
                    wait = max(0, float(error.next_post_at) - self.now())
                    if (
                        attempt < 9
                        and wait * 1_000 + MIN_REMAINING_MS
                        < context.get_remaining_time_in_millis()
                    ):
                        self.sleep(wait)
                        continue
                    raise
            response = self._request(
                "POST",
                f"/channels/{conversation}/messages",
                payload,
                event,
                context,
            )
            message_id = response.get("id")
            if not message_id:
                self.event = self.store.needs_review(event, "discord_invalid_response")
                raise PermanentDiscordError("Discord returned no message ID")
            event = self.store.posted(event, index, message_id)
            self.event = event
            if index + 1 < len(event["slack_parts"]):
                self.sleep(1)

        self.event = self.store.complete(event, event["slack_parts"][-1]["ts"])
        return self.event
