import json
import re
from slack_utils import handle_slack_event, send_slack_message, get_thread_history
from dynamodb_utils import save_initial_event, update_event
from bedrock_utils import invoke_claude_model, format_conversation_for_bedrock, generate_summary
from url_utils import get_url_content, prepare_summary_prompt
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

        # URLが含まれているかチェック
        if "http://" in message or "https://" in message:
            # URLを抽出し、余分な文字を削除
            url = re.search(r'(https?://\S+)', message).group(1).strip('<>')
            print(f"Extracted URL: {url}")  # 抽出したURLのログ出力
            url_content = get_url_content(url)
            print(f"URL content: {url_content}")  # URL内容のログ出力
            summary_prompt = prepare_summary_prompt(url_content)
            print(f"Summary prompt: {summary_prompt}")  # サマリープロンプトのログ出力
            ai_response = generate_summary(summary_prompt)
            print(f"AI response: {ai_response}")  # AI応答のログ出力
            
            response = f"ウェブページの要約は以下の通りです：\n\n{ai_response}"
        else:
            # 既存のClaude対話処理
            messages = format_conversation_for_bedrock(conversation_history, message)
            ai_response = invoke_claude_model(messages)
            response = ai_response

        # ログにAIの応答を出力
        print(f"AI response for user {user_id} in channel {channel_id}: {response}")

        # Slackにメッセージを送信（スレッド内）
        send_slack_message(channel_id, response, thread_ts)

        # DynamoDBのエントリを更新
        update_event(event_id, response)

        return {'statusCode': 200, 'body': json.dumps({'message': 'OK'})}

    except Exception as e:
        print(f"Unexpected error: {e}")
        return {'statusCode': 500, 'body': json.dumps({'error': 'Internal Server Error'})}
