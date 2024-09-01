import boto3
import json
import logging
from botocore.config import Config
from botocore.exceptions import ClientError
from config import *

logger = logging.getLogger()

# カスタムリトライ設定
custom_retry_config = Config(retries={"max_attempts": 8, "mode": "adaptive"})

bedrock_runtime = boto3.client(
    "bedrock-runtime", region_name="us-east-1", config=custom_retry_config
)


def invoke_claude_model(messages):
    body = json.dumps(
        {
            "anthropic_version": AI_MODEL_VERSION,
            "max_tokens": AI_MODEL_MAX_TOKENS,
            "messages": messages,
        }
    )

    # debug log
    logger.info(f"Messages: {json.dumps(messages, indent=2)}")

    try:
        response = bedrock_runtime.invoke_model(modelId=AI_MODEL_ID, body=body)

        response_body = json.loads(response["body"].read())
        return response_body["content"][0]["text"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ThrottlingException":
            logger.warning(
                "ThrottlingException occurred. Consider implementing a backoff strategy"
                " or reducing request frequency."
            )
        else:
            logger.error(f"Error invoking Bedrock model: {e}")
            if "ValidationException" in str(e):
                logger.error("Validation error. Check the format of the messages.")
        raise

    raise Exception("Failed to invoke Bedrock model")


def format_conversation_for_claude(conversation_history, append_message=None):
    formatted_messages = []
    assistant_response_count = 0
    last_role = None

    for msg in conversation_history:
        role = "assistant" if msg.get("bot_id") else "user"
        content = msg["text"]

        # ボットメンションを除去（Slackの履歴にはメンションが含まれている可能性があるため）
        if role == "user":
            content = content.split(">", 1)[-1].strip()

        if role == "assistant":
            assistant_response_count += 1

        # 同じロールが連続する場合、内容を結合する
        if role == last_role and formatted_messages:
            formatted_messages[-1]["content"] += f"\n{content}"
        else:
            formatted_messages.append({"role": role, "content": content})
            last_role = role

    # append_message が指定されている場合、最後のメッセージとして追加
    if append_message:
        if formatted_messages and formatted_messages[-1]["role"] == "user":
            formatted_messages[-1]["content"] += f"\n{append_message}"
        else:
            formatted_messages.append({"role": "user", "content": append_message})

    return formatted_messages, assistant_response_count
