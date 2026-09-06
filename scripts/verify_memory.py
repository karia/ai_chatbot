"""Run against the deployed verification Lambda; never create infrastructure."""

import json
import os
import time
import uuid

import boto3
from botocore.config import Config


def main():
    client = boto3.client(
        "lambda",
        region_name="ap-northeast-1",
        config=Config(read_timeout=40, retries={"total_max_attempts": 1}),
    )
    function = os.environ.get("LAMBDA_FUNCTION_NAME", "ai-chatbot-memory-verification")
    prefix = uuid.uuid4().hex

    def invoke(action, suffix, batch_size=1):
        response = client.invoke(
            FunctionName=function,
            Payload=json.dumps(
                {
                    "action": action,
                    "session_id": prefix + suffix,
                    "batch_size": batch_size,
                }
            ).encode(),
        )
        payload = json.load(response["Payload"])
        return response.get("FunctionError"), payload

    def restore(suffix, expected):
        for attempt in range(10):
            error, result = invoke("read", suffix)
            assert not error, result
            roles = [message["role"] for message in result["messages"]]
            if roles == expected:
                print(json.dumps({"case": suffix, "restored_roles": roles}), flush=True)
                return result
            time.sleep(1)
        raise AssertionError({"case": suffix, "expected": expected, "actual": result})

    def content(messages):
        return [{"role": m["role"], "content": m["content"]} for m in messages]

    error, first = invoke("chat", "normal")
    assert not error and first["restored"] == [], first
    result = restore("normal", ["user", "assistant"])
    assert content(result["messages"]) == content(first["messages"]), result
    error, second = invoke("chat", "normal")
    assert not error and content(second["restored"]) == content(first["messages"]), (
        second
    )
    restore("normal", ["user", "assistant", "user", "assistant"])
    restore("isolated", [])
    error, denied = invoke("denied", "permission")
    assert not error and denied["error_code"] == "AccessDeniedException", denied
    print(json.dumps({"case": "permission", **denied}), flush=True)
    error, timed = invoke("api_timeout", "apitimeout")
    assert not error and timed["error_type"] == "ReadTimeoutError", timed
    assert timed["elapsed_seconds"] < 10, timed
    print(json.dumps({"case": "api_timeout", **timed}), flush=True)
    error, batched = invoke("chat", "batchclose", 100)
    assert not error, batched
    restore("batchclose", ["user", "assistant"])
    for suffix, batch_size in [("immediate", 1), ("buffered", 100)]:
        error, failure = invoke("timeout", suffix, batch_size)
        assert error and failure.get("errorType") == "Sandbox.Timedout", failure
        print(
            json.dumps({"case": suffix, "error_type": failure["errorType"]}), flush=True
        )
        restore(suffix, ["user"] if batch_size == 1 else [])
    error, failure = invoke("exception", "exception", 100)
    assert error and failure.get("errorType") == "RuntimeError", failure
    restore("exception", ["user"])
    print("All memory verification checks passed.", flush=True)


if __name__ == "__main__":
    main()
