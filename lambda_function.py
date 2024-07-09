import json
from slack_utils import handle_slack_event, send_slack_message, get_thread_history
from dynamodb_utils import save_initial_event, update_event
from bedrock_utils import invoke_claude_model, format_conversation_for_bedrock
from config import DYNAMODB_TABLE_NAME

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

        # Slackイベントの処理
        channel_id, user_id, message, thread_ts = handle_slack_event(slack_event)

        # 仮のエントリをDynamoDBに保存
        if not save_initial_event(event_id, user_id, channel_id, thread_ts, message):
            print(f"Duplicate event detected: {event_id}")
            return {'statusCode': 200, 'body': json.dumps({'message': 'Duplicate event ignored'})}

        # スレッドの会話履歴を取得
        conversation_history = get_thread_history(channel_id, thread_ts)

        # ログに質問を出力
        print(f"Received question from user {user_id} in channel {channel_id}: {message}")

        # Amazon Bedrock経由でClaudeに問い合わせ
        messages = format_conversation_for_bedrock(conversation_history, message)
        ai_response = invoke_claude_model(messages)

        # ログにAIの応答を出力
        print(f"AI response for user {user_id} in channel {channel_id}: {ai_response}")

        # Slackにメッセージを送信（スレッド内）
        send_slack_message(channel_id, ai_response, thread_ts)

        # DynamoDBのエントリを更新
        update_event(event_id, ai_response)

        return {'statusCode': 200, 'body': json.dumps({'message': 'OK'})}

    except Exception as e:
        print(f"Unexpected error: {e}")
        return {'statusCode': 500, 'body': json.dumps({'error': 'Internal Server Error'})}
