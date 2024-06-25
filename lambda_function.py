import json
import os
import time
import boto3
from botocore.exceptions import ClientError
from anthropic import Anthropic
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from boto3.dynamodb.conditions import Key, Attr

# 環境変数の設定
ANTHROPIC_API_KEY = os.environ['ANTHROPIC_API_KEY']
SLACK_BOT_TOKEN = os.environ['SLACK_BOT_TOKEN']
DYNAMODB_TABLE_NAME = os.environ['DYNAMODB_TABLE_NAME']

# クライアントの初期化
anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
slack_client = WebClient(token=SLACK_BOT_TOKEN)
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE_NAME)

def lambda_handler(event, context):
    try:
        # Slack Event APIからのチャレンジレスポンスの処理
        if 'challenge' in event['body']:
            return {
                'statusCode': 200,
                'body': json.loads(event['body'])['challenge']
            }
        
        # イベントの解析
        body = json.loads(event['body'])
        slack_event = body['event']
        event_id = body['event_id']
        
        # app_mentionイベント以外は無視
        if slack_event['type'] != 'app_mention':
            return {'statusCode': 200, 'body': json.dumps({'message': 'OK'})}

        channel_id = slack_event['channel']
        user_id = slack_event['user']
        message = slack_event['text']
        thread_ts = slack_event.get('thread_ts', slack_event['ts'])

        # 仮のエントリをDynamoDBに保存
        if not save_initial_event(event_id, user_id, channel_id, thread_ts, message):
            print(f"Duplicate event detected: {event_id}")
            return {'statusCode': 200, 'body': json.dumps({'message': 'Duplicate event ignored'})}

        # メッセージからボットメンションを除去
        message = message.split('>', 1)[-1].strip()

        # スレッドの会話履歴を取得
        conversation_history = get_thread_history(channel_id, thread_ts)

        # ログに質問を出力
        print(f"Received question from user {user_id} in channel {channel_id}: {message}")

        # Anthropic APIに問い合わせ
        messages = format_conversation_for_anthropic(conversation_history, message)
        response = anthropic_client.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=1024,
            messages=messages
        )
        
        ai_response = response.content[0].text

        # ログにAIの応答を出力
        print(f"AI response for user {user_id} in channel {channel_id}: {ai_response}")

        # Slackにメッセージを送信（スレッド内）
        slack_client.chat_postMessage(
            channel=channel_id,
            text=ai_response,
            thread_ts=thread_ts
        )

        # DynamoDBのエントリを更新
        update_event(event_id, ai_response)

        return {'statusCode': 200, 'body': json.dumps({'message': 'OK'})}

    except SlackApiError as e:
        print(f"Error sending message to Slack: {e}")
        return {'statusCode': 500, 'body': json.dumps({'error': 'Error sending message to Slack'})}
    
    except ClientError as e:
        print(f"DynamoDB error: {e}")
        return {'statusCode': 500, 'body': json.dumps({'error': 'DynamoDB operation failed'})}
    
    except Exception as e:
        print(f"Unexpected error: {e}")
        return {'statusCode': 500, 'body': json.dumps({'error': 'Internal Server Error'})}

def save_initial_event(event_id, user_id, channel_id, thread_ts, user_message):
    timestamp = int(time.time() * 1000)
    try:
        table.put_item(
            Item={
                'event_id': event_id,
                'user_id': user_id,
                'timestamp': timestamp,
                'channel_id': channel_id,
                'thread_ts': thread_ts,
                'user_message': user_message,
                'status': 'processing'
            },
            ConditionExpression='attribute_not_exists(event_id)'
        )
        print(f"Initial entry saved to DynamoDB: event_id={event_id}")
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            print(f"Duplicate event detected during initial save: {event_id}")
            return False
        else:
            raise

def update_event(event_id, ai_response):
    try:
        table.update_item(
            Key={'event_id': event_id},
            UpdateExpression="set ai_response = :r, #s = :c",
            ExpressionAttributeValues={
                ':r': ai_response,
                ':c': 'completed'
            },
            ExpressionAttributeNames={
                '#s': 'status'
            },
            ConditionExpression=Attr('status').eq('processing')
        )
        print(f"Event updated in DynamoDB: event_id={event_id}")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            print(f"Event already processed: {event_id}")
        else:
            raise

def get_thread_history(channel_id, thread_ts):
    try:
        response = slack_client.conversations_replies(
            channel=channel_id,
            ts=thread_ts
        )
        return response['messages']
    except SlackApiError as e:
        print(f"Error fetching thread history: {e}")
        return []

def format_conversation_for_anthropic(conversation_history, current_message):
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
