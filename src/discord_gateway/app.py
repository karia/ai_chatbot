import asyncio
import json
import logging
import os
import time

import boto3
import discord


MAX_SEND_FAILURES = 3


def emit(level, state, **fields):
    threshold = logging.getLevelNamesMapping().get(
        os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO
    )
    if level >= threshold:
        print(
            json.dumps(
                {
                    "level": logging.getLevelName(level),
                    "component": "discord_gateway",
                    "state": state,
                    **fields,
                }
            ),
            flush=True,
        )


def should_handle(message, bot_id, guild_ids, channel_ids):
    if message.guild is None or message.guild.id not in guild_ids:
        return False
    channel_id = getattr(message.channel, "parent_id", None) or message.channel.id
    return (
        channel_id in channel_ids
        and any(user.id == bot_id for user in message.mentions)
        and not message.author.bot
        and message.webhook_id is None
        and not message.is_system()
    )


def to_queue_message(message, bot_id, received_at):
    in_thread = getattr(message.channel, "parent_id", None) is not None
    event_id = str(message.id)
    channel_id = str(message.channel.id)
    text = (
        message.content.replace(f"<@{bot_id}>", "")
        .replace(f"<@!{bot_id}>", "")
        .strip()
    )
    return {
        "schema_version": 1,
        "platform": "discord",
        "event_id": event_id,
        "guild_id": str(message.guild.id),
        "channel_id": channel_id,
        "conversation_id": channel_id if in_thread else event_id,
        "create_thread": not in_thread,
        "user_id": str(message.author.id),
        "text": text,
        "received_at": received_at,
    }


def queue_ids(message):
    return (
        f'discord:conversation:{message["guild_id"]}:{message["conversation_id"]}',
        f'discord:event:{message["event_id"]}',
    )


class QueueSender:
    def __init__(self, sqs, queue_url, exit_process=os._exit):
        self.sqs = sqs
        self.queue_url = queue_url
        self.exit_process = exit_process
        self.failures = 0

    async def send(self, message):
        group_id, deduplication_id = queue_ids(message)
        try:
            result = await asyncio.to_thread(
                self.sqs.send_message,
                QueueUrl=self.queue_url,
                MessageBody=json.dumps(message, separators=(",", ":")),
                MessageGroupId=group_id,
                MessageDeduplicationId=deduplication_id,
            )
            if not result.get("MessageId"):
                raise RuntimeError("SQS did not return a message ID")
        except Exception as error:
            self.failures += 1
            emit(
                logging.ERROR,
                "queue_failed",
                event_id=message["event_id"],
                error_class=type(error).__name__,
                consecutive_failures=self.failures,
            )
            if self.failures >= MAX_SEND_FAILURES:
                emit(
                    logging.ERROR,
                    "send_failure_limit",
                    event_id=message["event_id"],
                    consecutive_failures=self.failures,
                )
                self.exit_process(1)
            return False
        self.failures = 0
        emit(logging.INFO, "queued", event_id=message["event_id"])
        return True


def _required(name):
    value = os.environ[name]
    if not value:
        raise ValueError(f"{name} is empty")
    return value


def _ids(name):
    values = {int(value.strip()) for value in _required(name).split(",")}
    if any(value <= 0 for value in values):
        raise ValueError(f"{name} contains an invalid ID")
    return values


def main():
    try:
        token = _required("DISCORD_BOT_TOKEN")
        queue_url = _required("QUEUE_URL")
        guild_ids = _ids("ALLOWED_GUILD_IDS")
        channel_ids = _ids("ALLOWED_CHANNEL_IDS")
    except (KeyError, ValueError) as error:
        emit(logging.ERROR, "invalid_configuration", error_class=type(error).__name__)
        raise SystemExit(1) from error

    sqs = boto3.client("sqs")
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    client = discord.Client(intents=intents)
    sender = QueueSender(sqs, queue_url)

    @client.event
    async def on_ready():
        emit(logging.INFO, "ready", bot_user_id=str(client.user.id))

    @client.event
    async def on_message(message):
        if client.user is None or not should_handle(
            message, client.user.id, guild_ids, channel_ids
        ):
            return
        queued = to_queue_message(message, client.user.id, int(time.time()))
        await sender.send(queued)

    client.run(token, log_handler=None)


if __name__ == "__main__":
    main()
