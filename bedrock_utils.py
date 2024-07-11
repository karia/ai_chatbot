import boto3
import json
import logging
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()

# カスタムリトライ設定
custom_retry_config = Config(
    retries={
        'max_attempts': 8,
        'mode': 'adaptive'
    }
)

bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1', config=custom_retry_config)

def invoke_claude_model(messages):
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": messages
    })
    
    try:
        response = bedrock_runtime.invoke_model(
            modelId="anthropic.claude-3-5-sonnet-20240620-v1:0",
            body=body
        )
        
        response_body = json.loads(response['body'].read())
        return response_body['content'][0]['text']
    except ClientError as e:
        if e.response['Error']['Code'] == 'ThrottlingException':
            logger.warning("ThrottlingException occurred. Consider implementing a backoff strategy or reducing request frequency.")
        else:
            logger.error(f"Error invoking Bedrock model: {e}")
            if 'ValidationException' in str(e):
                logger.error("Validation error. Check the format of the messages.")
                logger.error(f"Messages: {json.dumps(messages, indent=2)}")
        raise

    raise Exception("Failed to invoke Bedrock model")

def format_conversation_for_claude(conversation_history, current_message):
    formatted_messages = []
    current_role = None
    current_content = []

    for msg in conversation_history:
        role = "assistant" if msg.get('bot_id') else "user"
        content = msg['text']

        if role == current_role:
            current_content.append(content)
        else:
            if current_role:
                formatted_messages.append({"role": current_role, "content": "\n".join(current_content)})
            current_role = role
            current_content = [content]

    # Add the last message from the conversation history
    if current_role:
        formatted_messages.append({"role": current_role, "content": "\n".join(current_content)})

    # Add the current message
    if formatted_messages and formatted_messages[-1]["role"] == "user":
        formatted_messages[-1]["content"] += f"\n{current_message}"
    else:
        formatted_messages.append({"role": "user", "content": current_message})

    return formatted_messages
