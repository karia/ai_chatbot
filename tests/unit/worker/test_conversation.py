import asyncio
from types import SimpleNamespace

import pytest
from bedrock_agentcore.memory.integrations.strands.bedrock_converter import AgentCoreMemoryConverter
from strands.types.session import SessionMessage

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


def memory_event(role, content, event_id="Ev1"):
    text, converted_role = AgentCoreMemoryConverter.message_to_payload(
        SessionMessage.from_message({"role": role, "content": content}, 0)
    )[0]
    return {
        "metadata": {"event_id": {"stringValue": event_id}},
        "payload": [{"conversational": {"content": {"text": text}, "role": converted_role.upper()}}],
    }


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
                memory_event("user", [{"text": "Slack user U1: hello"}]),
                memory_event("assistant", [{"text": "answer"}]),
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
    assert agent["model"]["max_tokens"] == conversation.MAX_OUTPUT_TOKENS
    invoke = next(call for call in calls if call[0] == "invoke")
    assert invoke[2]["limits"]["turns"] == conversation.MAX_MODEL_TURNS
    assert invoke[2]["limits"]["output_tokens"] == conversation.MAX_OUTPUT_TOKENS * conversation.MAX_MODEL_TURNS


def test_saved_turn_reads_converter_payload_and_rejects_other_events():
    events = [
        memory_event("user", [{"text": "Slack user U1: hello"}]),
        memory_event("assistant", [{"text": "wrong"}], event_id="Ev2"),
        memory_event("assistant", [{"text": "answer"}]),
    ]
    assert conversation._saved_turn(events, "Ev1", "Slack user U1: hello", "answer")
    assert not conversation._saved_turn(events, "Ev2", "Slack user U1: hello", "answer")


@pytest.mark.parametrize("reason", ["max_tokens", "limit_turns", "limit_output_tokens", "limit_total_tokens"])
@pytest.mark.parametrize("text", ["partial answer", ""])
def test_limit_stop_returns_partial_text_and_notice_after_memory_confirmation(monkeypatch, reason, text):
    calls = []

    class Memory:
        def __init__(self, config):
            self.memory_client = self

        def list_events(self, **kwargs):
            calls.append("list")
            if len(calls) == 1:
                return []
            events = [memory_event("user", [{"text": "Slack user U1: hello"}])]
            if text:
                events.append(memory_event("assistant", [{"text": text}]))
            return events

        def close(self):
            calls.append("close")

    monkeypatch.setenv("MEMORY_ID", "memory-test")
    monkeypatch.setattr(conversation, "AgentCoreMemorySessionManager", Memory)
    monkeypatch.setattr(conversation, "BedrockModel", lambda **kwargs: kwargs)
    monkeypatch.setattr(conversation, "Agent", lambda **kwargs: lambda *args, **kwargs: SimpleNamespace(
        stop_reason=reason, message={"content": [{"text": text}]},
        metrics=SimpleNamespace(cycles=[], usage={}, tool_metrics={}),
    ))

    expected = f"{text}\n\n{conversation.LIMIT_STOP_NOTICE}" if text else conversation.LIMIT_STOP_NOTICE
    assert conversation.generate(message()) == expected
    assert calls == ["list", "close", "list"]


def test_limit_stop_still_rejects_unconfirmed_partial_answer(monkeypatch):
    class Memory:
        def __init__(self, config):
            self.memory_client = self

        def list_events(self, **kwargs):
            return []

        def close(self):
            pass

    monkeypatch.setenv("MEMORY_ID", "memory-test")
    monkeypatch.setattr(conversation, "AgentCoreMemorySessionManager", Memory)
    monkeypatch.setattr(conversation, "BedrockModel", lambda **kwargs: kwargs)
    monkeypatch.setattr(conversation, "Agent", lambda **kwargs: lambda *args, **kwargs: SimpleNamespace(
        stop_reason="limit_turns", message={"content": [{"text": "partial answer"}]},
    ))
    with pytest.raises(conversation.MemoryUnconfirmed):
        conversation.generate(message())


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


def test_url_and_attachment_reads_share_tool_budget(monkeypatch):
    reads = []
    monkeypatch.setattr(conversation, "fetch_url", lambda url: reads.append(url) or {"text": "ok"})
    url_tool, attachment_tool = conversation._tools(message())

    async def invoke():
        results = []
        for index, selected in enumerate([url_tool, attachment_tool, url_tool, attachment_tool]):
            tool_input = {"url": "https://example.com"} if selected is url_tool else {}
            async for result in selected.stream(
                {"toolUseId": str(index), "name": selected.tool_name, "input": tool_input}, {}
            ):
                results.append(result)
        return results

    results = asyncio.run(invoke())
    assert len(reads) == 2
    assert "tool_limit" not in str(results[2])
    assert "tool_limit" in str(results[-1])


def test_conversation_manager_keeps_tool_pair_when_trimming():
    manager = conversation.SlidingWindowConversationManager(
        window_size=12, per_turn=True, proactive_compression=True
    )
    tool_use = {"role": "assistant", "content": [{"toolUse": {"toolUseId": "call-1", "name": "read_url", "input": {}}}]}
    tool_result = {"role": "user", "content": [{"toolResult": {"toolUseId": "call-1", "content": [{"text": "ok"}]}}]}
    agent = SimpleNamespace(messages=[
        tool_use, tool_result,
        *[{"role": "user", "content": [{"text": str(index)}]} for index in range(11)],
    ])

    manager.apply_management(agent)

    assert len(agent.messages) < 13
    assert (tool_use in agent.messages) == (tool_result in agent.messages)
    assert agent.messages[0]["role"] == "user"
