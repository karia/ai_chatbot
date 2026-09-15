import asyncio
import json
from types import SimpleNamespace

import pytest

from worker import conversation


def message(**changes):
    return {
        "event_id": "Ev1",
        "team_id": "T1",
        "channel_id": "C1",
        "thread_ts": "1800000000.000001",
        "message_ts": "1800000000.000002",
        "user_id": "U1",
        "text": "hello",
        "file_ids": [],
    } | changes


def test_ids_share_thread_across_users_and_isolate_channels(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "dev")
    first = conversation.memory_ids(message())
    second = conversation.memory_ids(message(user_id="U2"))
    other = conversation.memory_ids(message(channel_id="C2"))

    assert first == second
    assert first[0] != other[0] and first[1] != other[1]
    assert all(len(value) == 64 for value in first)


@pytest.mark.parametrize("model_id", ["global.anthropic.claude-opus-5", "custom.model"])
def test_generate_confirms_memory_save_and_uses_budgets(monkeypatch, model_id):
    calls = []

    class Memory:
        def __init__(self, config, **kwargs):
            self.config = config
            self.memory_client = self
            calls.append(("memory", config))

        def list_events(self, **kwargs):
            calls.append(("list", kwargs))
            return [] if len([call for call in calls if call[0] == "list"]) == 1 else [
                {"metadata": {"event_id": {"stringValue": "Ev1"}},
                 "payload": [{"conversational": {"content": {"text": json.dumps({"role": "user", "content": "Slack user U1: hello"})}}}]},
                {"metadata": {"event_id": {"stringValue": "Ev1"}},
                 "payload": [{"conversational": {"content": {"text": json.dumps({"role": "assistant", "content": "answer"})}}}]},
            ]

        def close(self):
            calls.append(("close",))

    class Agent:
        def __init__(self, **kwargs):
            calls.append(("agent", kwargs))

        def __call__(self, prompt, **kwargs):
            calls.append(("invoke", prompt, kwargs))
            return SimpleNamespace(
                stop_reason="end_turn",
                message={"content": [{"text": "answer"}]},
                metrics=SimpleNamespace(
                    cycles=[1], usage={"outputTokens": 20}, tool_metrics={}
                ),
            )

    monkeypatch.setenv("MEMORY_ID", "memory-test")
    monkeypatch.setenv("BEDROCK_MODEL_ID", model_id)
    monkeypatch.setattr(conversation, "AgentCoreMemorySessionManager", Memory)
    monkeypatch.setattr(conversation, "Agent", Agent)
    monkeypatch.setattr(conversation, "BedrockModel", lambda **kwargs: kwargs)

    assert conversation.generate(message()) == "answer"
    assert calls[-1][0] == "list"
    assert calls[-2] == ("close",)
    agent = next(call[1] for call in calls if call[0] == "agent")
    assert agent["agent_id"] == "conversation"
    assert len(agent["tools"]) == 2
    assert agent["model"]["model_id"] == model_id
    invoke = next(call for call in calls if call[0] == "invoke")
    assert invoke[2]["limits"]["turns"] > 0
    assert invoke[2]["limits"]["output_tokens"] > 0


def test_incomplete_memory_save_is_not_accepted(monkeypatch):
    class Memory:
        def __init__(self, config, **kwargs):
            self.memory_client = self

        def list_events(self, **kwargs):
            return []

        def close(self):
            pass

    monkeypatch.setenv("MEMORY_ID", "memory-test")
    monkeypatch.setattr(conversation, "AgentCoreMemorySessionManager", Memory)
    monkeypatch.setattr(
        conversation, "Agent", lambda **kwargs: lambda *args, **kwargs: SimpleNamespace(
            stop_reason="end_turn", message={"content": [{"text": "answer"}]},
            metrics=SimpleNamespace(cycles=[1], usage={"outputTokens": 1}, tool_metrics={}),
        ),
    )
    monkeypatch.setattr(conversation, "BedrockModel", lambda **kwargs: kwargs)

    with pytest.raises(conversation.MemoryUnconfirmed):
        conversation.generate(message())


def test_memory_read_timeout_prevents_model_call(monkeypatch):
    class Memory:
        def __init__(self, config, **kwargs):
            self.memory_client = self

        def list_events(self, **kwargs):
            raise TimeoutError("Memory unavailable")

        def close(self):
            pass

    monkeypatch.setenv("MEMORY_ID", "memory-test")
    monkeypatch.setattr(conversation, "AgentCoreMemorySessionManager", Memory)
    monkeypatch.setattr(conversation, "Agent", lambda **kwargs: pytest.fail("model created"))

    with pytest.raises(TimeoutError):
        conversation.generate(message())


def test_tool_budget_bounds_external_reads(monkeypatch):
    reads = []
    monkeypatch.setattr(conversation, "fetch_url", lambda url: reads.append(url) or {"text": "ok"})
    url_tool = conversation._tools(message())[0]

    async def invoke():
        results = []
        for index in range(4):
            async for result in url_tool.stream(
                {"toolUseId": str(index), "name": "read_url", "input": {"url": "https://example.com"}}, {}
            ):
                results.append(result)
        return results

    results = asyncio.run(invoke())
    assert len(reads) == conversation.MAX_TOOL_CALLS
    assert "tool_limit" in str(results[-1])
