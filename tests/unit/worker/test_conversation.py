import asyncio
from types import SimpleNamespace

import pytest
from bedrock_agentcore.memory.integrations.strands.bedrock_converter import AgentCoreMemoryConverter
from strands import Agent
from strands.hooks import MessageAddedEvent
from strands.models.model import Model
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


class MockAgent:
    def __init__(self, result):
        self.result = result
        self.messages = []
        self.callbacks = []
        self.hooks = SimpleNamespace(add_callback=lambda event, callback: self.callbacks.append(callback))

    def __call__(self, prompt, **kwargs):
        self.messages = [
            {"role": "user", "content": [{"text": prompt}]},
            {"role": "assistant", "content": self.result.message["content"]},
        ]
        for callback in self.callbacks:
            callback(SimpleNamespace(message=self.messages[-1]))
        return self.result


class FakeModel(Model):
    def __init__(self, responses):
        self.responses = iter(responses)

    def update_config(self, **model_config):
        pass

    def get_config(self):
        return {"model_id": "fake"}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise AssertionError("unexpected structured output")

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        reason, content = next(self.responses)
        yield {"messageStart": {"role": "assistant"}}
        for block in content:
            if "text" in block:
                yield {"contentBlockStart": {"start": {}}}
                yield {"contentBlockDelta": {"delta": {"text": block["text"]}}}
            else:
                tool = block["toolUse"]
                yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": tool["toolUseId"], "name": tool["name"]}}}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": "{}"}}}}
            yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": reason}}
        yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}}}


class FakeMemory:
    def __init__(self, config, drop_assistant=None):
        self.config = config
        self.memory_client = self
        self.events = []
        self.drop_assistant = drop_assistant
        self.assistant_saves = 0

    def register_hooks(self, registry):
        registry.add_callback(MessageAddedEvent, self.save_message)

    def save_message(self, event):
        if event.message["role"] == "assistant":
            self.assistant_saves += 1
            if self.assistant_saves == self.drop_assistant:
                return
        converted = AgentCoreMemoryConverter.message_to_payload(
            SessionMessage.from_message(event.message, len(self.events))
        )
        for text, role in converted:
            self.events.append({
                "metadata": {"event_id": self.config.default_metadata["event_id"]},
                "payload": [{"conversational": {"content": {"text": text}, "role": role.upper()}}],
            })

    def list_events(self, **kwargs):
        return self.events

    def close(self):
        pass


@pytest.mark.parametrize("responses,turns,expected", [
    ([("max_tokens", [{"text": "partial"}])], 4, "partial"),
    ([("max_tokens", [])], 4, ""),
    ([("tool_use", [{"toolUse": {"toolUseId": "t1", "name": "read_url", "input": {}}}])], 1, ""),
    ([("tool_use", [{"text": "preamble"}, {"toolUse": {"toolUseId": "t1", "name": "read_url", "input": {}}}])], 1, "preamble"),
    ([
        ("tool_use", [{"text": "first"}, {"toolUse": {"toolUseId": "t1", "name": "read_url", "input": {}}}]),
        ("tool_use", [{"text": "second"}, {"toolUse": {"toolUseId": "t2", "name": "read_url", "input": {}}}]),
    ], 2, "first\nsecond"),
])
def test_real_agent_limit_saves_and_confirms_partial_reply(monkeypatch, responses, turns, expected):
    memories = []
    agents = []

    def make_memory(config):
        memory = FakeMemory(config)
        memories.append(memory)
        return memory

    def make_agent(**kwargs):
        agent = Agent(**kwargs)
        agents.append(agent)
        return agent

    monkeypatch.setenv("MEMORY_ID", "memory-test")
    monkeypatch.setattr(conversation, "AgentCoreMemorySessionManager", make_memory)
    monkeypatch.setattr(conversation, "BedrockModel", lambda **kwargs: FakeModel(responses))
    monkeypatch.setattr(conversation, "Agent", make_agent)
    monkeypatch.setattr(conversation, "MAX_MODEL_TURNS", turns)
    monkeypatch.setattr(conversation, "fetch_url", lambda url: {"text": "ok"})

    if responses == [("max_tokens", [])]:
        with pytest.raises(conversation.MemoryUnconfirmed):
            conversation.generate(message())
    else:
        reply = conversation.generate(message())
        assert reply == (f"{expected}\n\n{conversation.LIMIT_STOP_NOTICE}" if expected else conversation.LIMIT_STOP_NOTICE)
        assistant_messages = [item for item in agents[0].messages if item["role"] == "assistant"]
        assert conversation._saved_turn(memories[0].events, "Ev1", "Slack user U1: hello", assistant_messages)
    assert agents[0].messages[-1]["role"] == ("assistant" if responses[-1][0] == "max_tokens" else "user")


@pytest.mark.parametrize("content", [
    [{"toolUse": {"toolUseId": "t1", "name": "read_url", "input": {}}}],
    [{"text": "answer"}],
])
def test_real_agent_rejects_dropped_assistant_save(monkeypatch, content):
    monkeypatch.setenv("MEMORY_ID", "memory-test")
    monkeypatch.setattr(conversation, "AgentCoreMemorySessionManager", lambda config: FakeMemory(config, drop_assistant=1))
    responses = [("tool_use", content), ("end_turn", [{"text": "answer"}])] if "toolUse" in content[0] else [("end_turn", content)]
    monkeypatch.setattr(conversation, "BedrockModel", lambda **kwargs: FakeModel(responses))
    monkeypatch.setattr(conversation, "fetch_url", lambda url: {"text": "ok"})

    with pytest.raises(conversation.MemoryUnconfirmed):
        conversation.generate(message())


def test_real_agent_confirms_assistants_trimmed_during_invocation(monkeypatch):
    agents = []
    responses = [
        ("tool_use", [{"text": "first"}, {"toolUse": {"toolUseId": "t1", "name": "read_url", "input": {}}}]),
        ("tool_use", [{"text": "second"}, {"toolUse": {"toolUseId": "t2", "name": "read_url", "input": {}}}]),
        ("end_turn", [{"text": "answer"}]),
    ]

    def make_agent(**kwargs):
        kwargs["conversation_manager"].window_size = 3
        agent = Agent(**kwargs)
        agents.append(agent)
        return agent

    monkeypatch.setenv("MEMORY_ID", "memory-test")
    monkeypatch.setattr(conversation, "AgentCoreMemorySessionManager", FakeMemory)
    monkeypatch.setattr(conversation, "BedrockModel", lambda **kwargs: FakeModel(responses))
    monkeypatch.setattr(conversation, "Agent", make_agent)
    monkeypatch.setattr(conversation, "fetch_url", lambda url: {"text": "ok"})

    assert conversation.generate(message()) == "answer"
    assert not any("first" in str(item.get("content")) for item in agents[0].messages)


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
            self.callbacks = []
            self.hooks = SimpleNamespace(add_callback=lambda event, callback: self.callbacks.append(callback))

        def __call__(self, prompt, **kwargs):
            calls.append(("invoke", prompt, kwargs))
            for callback in self.callbacks:
                callback(SimpleNamespace(message={"role": "assistant", "content": [{"text": "answer"}]}))
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
    assistant = [{"role": "assistant", "content": [{"text": "answer"}]}]
    assert conversation._saved_turn(events, "Ev1", "Slack user U1: hello", assistant)
    assert not conversation._saved_turn(events, "Ev2", "Slack user U1: hello", assistant)


def test_saved_turn_requires_every_assistant_record_even_without_text():
    events = [memory_event("user", [{"text": "Slack user U1: hello"}])]
    assistant = [{"role": "assistant", "content": [{"toolUse": {"toolUseId": "t1", "name": "read_url", "input": {}}}]}]
    assert not conversation._saved_turn(events, "Ev1", "Slack user U1: hello", assistant)

    repeated = [{"role": "assistant", "content": [{"text": "answer"}]}] * 2
    assert not conversation._saved_turn(events + [memory_event("assistant", [{"text": "answer"}])], "Ev1", "Slack user U1: hello", repeated)


@pytest.mark.parametrize("reason", ["limit_turns", "limit_output_tokens", "limit_total_tokens"])
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
    monkeypatch.setattr(conversation, "Agent", lambda **kwargs: MockAgent(SimpleNamespace(
        stop_reason=reason, message={"content": [{"text": text}]},
        metrics=SimpleNamespace(cycles=[], usage={}, tool_metrics={}),
    )))

    expected = f"{text}\n\n{conversation.LIMIT_STOP_NOTICE}" if text else conversation.LIMIT_STOP_NOTICE
    if text:
        assert conversation.generate(message()) == expected
    else:
        with pytest.raises(conversation.MemoryUnconfirmed):
            conversation.generate(message())
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
    monkeypatch.setattr(conversation, "Agent", lambda **kwargs: MockAgent(SimpleNamespace(
        stop_reason="limit_turns", message={"content": [{"text": "partial answer"}]},
    )))
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
    monkeypatch.setattr(conversation, "Agent", lambda **kwargs: MockAgent(SimpleNamespace(
        stop_reason="end_turn", message={"content": [{"text": "answer"}]},
    )))
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
