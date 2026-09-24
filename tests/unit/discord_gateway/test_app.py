from types import SimpleNamespace

import pytest

from discord_gateway.app import queue_ids, should_handle, to_queue_message


BOT_ID = 100
GUILD_ID = 200
CHANNEL_ID = 300
THREAD_ID = 400


def message(**changes):
    values = {
        "id": 500,
        "guild": SimpleNamespace(id=GUILD_ID),
        "channel": SimpleNamespace(id=CHANNEL_ID),
        "author": SimpleNamespace(id=600, bot=False),
        "mentions": [SimpleNamespace(id=BOT_ID)],
        "webhook_id": None,
        "content": f"<@{BOT_ID}> hello",
        "is_system": lambda: False,
    }
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    "changes",
    [
        {"guild": None},
        {"guild": SimpleNamespace(id=999)},
        {"channel": SimpleNamespace(id=999)},
        {"mentions": []},
        {"author": SimpleNamespace(id=600, bot=True)},
        {"webhook_id": 700},
        {"is_system": lambda: True},
    ],
)
def test_should_handle_rejects_out_of_scope_messages(changes):
    assert not should_handle(
        message(**changes), BOT_ID, {GUILD_ID}, {CHANNEL_ID}
    )


def test_should_handle_accepts_allowed_channel_and_child_thread():
    assert should_handle(message(), BOT_ID, {GUILD_ID}, {CHANNEL_ID})
    thread = SimpleNamespace(id=THREAD_ID, parent_id=CHANNEL_ID)
    assert should_handle(
        message(channel=thread), BOT_ID, {GUILD_ID}, {CHANNEL_ID}
    )


def test_to_queue_message_converts_channel_message():
    assert to_queue_message(message(), BOT_ID, 1_234) == {
        "schema_version": 1,
        "platform": "discord",
        "event_id": "500",
        "guild_id": "200",
        "channel_id": "300",
        "conversation_id": "500",
        "create_thread": True,
        "user_id": "600",
        "text": "hello",
        "received_at": 1_234,
    }


def test_to_queue_message_converts_thread_message_and_removes_all_bot_mentions():
    thread = SimpleNamespace(id=THREAD_ID, parent_id=CHANNEL_ID)
    source = message(
        channel=thread,
        content=f" <@{BOT_ID}> hello <@!{BOT_ID}> ",
    )

    converted = to_queue_message(source, BOT_ID, 1_234)

    assert converted["channel_id"] == "400"
    assert converted["conversation_id"] == "400"
    assert converted["create_thread"] is False
    assert converted["text"] == "hello"


def test_queue_ids_cannot_match_slack_hash_ids():
    group_id, deduplication_id = queue_ids(to_queue_message(message(), BOT_ID, 1_234))

    assert group_id == "discord:conversation:200:500"
    assert deduplication_id == "discord:event:500"
