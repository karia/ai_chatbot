import json
import os
import time
import boto3
from anthropic import Anthropic
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from boto3.dynamodb.conditions import Key

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
    
    # メンション以外のメッセージは無視
    if slack_event['type'] != 'app_mention':
        return {'statusCode': 200, 'body': 'OK'}

    # イベントの重複チェック
    if is_duplicate_event(event_id):
        print(f"Duplicate event detected: {event_id}")
        return {'statusCode': 200, 'body': 'OK'}

    channel_id = slack_event['channel']
    user_id = slack_event['user']
    message = slack_event['text']
    thread_ts = slack_event.get('thread_ts', slack_event['ts'])

    # ユーザーメッセージからボットメンションを除去
    message = message.split('>', 1)[-1].strip()

    # スレッドの会話履歴を取得
    conversation_history = get_thread_history(channel_id, thread_ts)

    # ログに質問を出力
    print(f"Received question from user {user_id} in channel {channel_id}: {message}")

    try:
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
        slack_response = slack_client.chat_postMessage(
            channel=channel_id,
            text=ai_response,
            thread_ts=thread_ts
        )

        # DynamoDBに会話を保存
        timestamp = int(time.time() * 1000)
        table.put_item(
            Item={
                'user_id': user_id,
                'timestamp': timestamp,
                'channel_id': channel_id,
                'thread_ts': thread_ts,
                'user_message': message,
                'ai_response': ai_response,
                'event_id': event_id
            }
        )

        # ログにDynamoDBへの保存を記録
        print(f"Conversation saved to DynamoDB: user_id={user_id}, timestamp={timestamp}, thread_ts={thread_ts}, event_id={event_id}")

        return {'statusCode': 200, 'body': 'OK'}

    except SlackApiError as e:
        print(f"Error sending message to Slack: {e}")
        return {'statusCode': 500, 'body': 'Error sending message to Slack'}
    
    except Exception as e:
        print(f"Unexpected error: {e}")
        return {'statusCode': 500, 'body': 'Internal Server Error'}

def is_duplicate_event(event_id):
    response = table.query(
        KeyConditionExpression=Key('event_id').eq(event_id)
    )
    return len(response['Items']) > 0

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
    for msg in conversation_history:
        if msg.get('bot_id'):
            messages.append({"role": "assistant", "content": msg['text']})
        else:
            messages.append({"role": "user", "content": msg['text']})
    
    # 現在のメッセージを追加
    messages.append({"role": "user", "content": current_message})
    
    return messages
