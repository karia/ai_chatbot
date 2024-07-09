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

def format_conversation_for_bedrock(conversation_history, current_message):
    messages = []
    last_role = None
    for msg in conversation_history:
        role = "assistant" if msg.get('bot_id') else "user"
        content = msg['text']
        
        # ユーザーメッセージからボットメンションを除去
        if role == "user":
            content = content.split('>', 1)[-1].strip()
        
        # 同じロールが連続する場合、内容を結合する
        if role == last_role and messages:
            messages[-1]["content"] += "\n" + content
        else:
            messages.append({"role": role, "content": content})
        
        last_role = role

    # 現在のメッセージを追加（ただし、最後のメッセージがuserでない場合のみ）
    if not messages or messages[-1]["role"] != "user":
        messages.append({"role": "user", "content": current_message})
    else:
        messages[-1]["content"] += "\n" + current_message

    return messages
