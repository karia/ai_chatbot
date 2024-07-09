import boto3
import json
from botocore.exceptions import ClientError

bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')

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
        print(f"Error invoking Bedrock model: {e}")
        error_message = str(e)
        if 'ValidationException' in error_message:
            print("Validation error. Check the format of the messages.")
            print(f"Messages: {json.dumps(messages, indent=2)}")
        raise

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
