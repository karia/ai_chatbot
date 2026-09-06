"""Deterministic Strands conversations against the real AgentCore Memory service."""

import asyncio
import json
import os
import time

import boto3
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError
from strands import Agent
from strands.models import Model


class VerificationModel(Model):
    """Emit a fixed stream so persistence and interruption checks are reproducible."""

    def __init__(self, failure):
        self.failure = failure

    def get_config(self):
        return {}

    def update_config(self, **kwargs):
        raise NotImplementedError

    def structured_output(self, *args, **kwargs):
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        yield {"messageStart": {"role": "assistant"}}
        yield {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"text": "acknowledged"},
            }
        }
        if self.failure:
            print(
                json.dumps(
                    {"checkpoint": "partial_assistant", "failure": self.failure}
                ),
                flush=True,
            )
            if self.failure == "timeout":
                await asyncio.sleep(60)
            raise RuntimeError("verification interruption")
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "end_turn"}}


def lambda_handler(event, context):
    action = event.get("action", "read")
    if action not in {"read", "chat", "timeout", "exception", "denied", "api_timeout"}:
        raise ValueError("unknown action")
    session_id = event.get("session_id")
    if (
        not isinstance(session_id, str)
        or not session_id.isalnum()
        or len(session_id) > 100
    ):
        raise ValueError("session_id must be 1-100 alphanumeric characters")
    batch_size = event.get("batch_size", 1)
    if batch_size not in (1, 100):
        raise ValueError("batch_size must be 1 or 100")
    if action == "api_timeout":
        client = boto3.client(
            "bedrock-agentcore",
            config=Config(
                connect_timeout=2,
                read_timeout=0.000001,
                retries={"total_max_attempts": 1},
            ),
        )
        started = time.monotonic()
        try:
            client.list_events(
                memoryId=os.environ["MEMORY_ID"],
                actorId="verification",
                sessionId=session_id,
            )
        except ReadTimeoutError:
            return {
                "error_type": "ReadTimeoutError",
                "elapsed_seconds": time.monotonic() - started,
            }
        raise AssertionError("read unexpectedly completed within one microsecond")
    if action == "denied":
        try:
            boto3.client("bedrock-agentcore-control").get_memory(
                memoryId=os.environ["MEMORY_ID"]
            )
        except ClientError as error:
            return {"error_code": error.response["Error"]["Code"]}
        raise AssertionError("control-plane access unexpectedly allowed")
    config = AgentCoreMemoryConfig(
        memory_id=os.environ["MEMORY_ID"],
        actor_id="verification",
        session_id=session_id,
        batch_size=batch_size,
    )
    with AgentCoreMemorySessionManager(
        config,
        region_name=os.environ["AWS_REGION"],
        boto_client_config=Config(
            connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 1}
        ),
    ) as manager:
        agent = Agent(
            model=VerificationModel(
                action if action in {"timeout", "exception"} else None
            ),
            session_manager=manager,
            agent_id="verification",
            callback_handler=None,
        )
        restored = list(agent.messages)
        if action != "read":
            agent("Remember the verification marker: sapphire.")
        return {"restored": restored, "messages": agent.messages}
