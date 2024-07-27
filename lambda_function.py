import json
import logging
from slack_utils import handle_slack_event, send_slack_message, get_thread_history
from dynamodb_utils import save_initial_event, update_event
from bedrock_utils import invoke_claude_model, format_conversation_for_claude
from url_utils import get_url_content
from utils import create_error_message, extract_url
from config import DYNAMODB_TABLE_NAME

# ロガーの設定
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    try:
        # デバッグ: リクエスト全体をログに出力
        logger.info(f"Received event: {json.dumps(event, indent=2)}")

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

        # デバッグ: パースされたbodyをログに出力
        logger.info(f"Parsed body: {json.dumps(body, indent=2)}")
        
        # app_mentionイベント以外は無視
        if slack_event['type'] != 'app_mention':
            return {'statusCode': 200, 'body': json.dumps({'message': 'OK'})}

        # Slackイベントの処理
        channel_id, user_id, message, thread_ts = handle_slack_event(slack_event)

        # 最新のユーザーメッセージをログに記録
        logger.info(f"User message: {message}")

        # 仮のエントリをDynamoDBに保存
        if not save_initial_event(event_id, user_id, channel_id, thread_ts, message):
            logger.info(f"Duplicate event detected: {event_id}")
            return {'statusCode': 200, 'body': json.dumps({'message': 'Duplicate event ignored'})}

        # スレッドの会話履歴を取得
        conversation_history = get_thread_history(channel_id, thread_ts)

        # デバッグ: スレッドの内容をログに出力
        logger.info(f"Thread history: {json.dumps(conversation_history, indent=2)}")

        # 最新のメッセージを除外（handle_slack_eventで既に処理済み）
        latest_message = conversation_history.pop()

        # URLの抽出
        url = extract_url(latest_message['text'])

        append_message = None
        response = None

        if url:
            try:
                url_title, url_content = get_url_content(url)
                
                # URLのみの場合とそうでない場合で処理を分ける
                if latest_message['text'].strip() == f"<{url}>":
                    append_message = f"上記URLのウェブページの内容を以下に示しますので、簡潔に要約してください。要約の冒頭に「ウェブページの要約は以下の通りです；」と1行追加してください。：\n\nタイトル:{url_title}\n本文:{url_content}"
                else:
                    append_message = f"以下はURLの内容です：\n\nタイトル:{url_title}\n本文:{url_content}"
            except Exception as e:
                error_message = create_error_message("URL処理", str(e))
                logger.error(error_message)
                response = error_message

        if response is None:  # URL処理で response が設定されていない場合
            messages, assistant_response_count = format_conversation_for_claude(conversation_history, append_message)
            
            # 最新のメッセージを追加
            messages.append({"role": "user", "content": latest_message['text']})
            
            if assistant_response_count >= 50:
                response = "申し訳ありませんが、このスレッドでの回答回数が制限を超えました。新しいスレッドで質問していただくようお願いいたします。"
            else:
                ai_response = invoke_claude_model(messages)
                response = ai_response

        # AIの応答をログに記録
        logger.info(f"AI response: {response}")

        # Slackにメッセージを送信（スレッド内）
        send_slack_message(channel_id, response, thread_ts)

        # DynamoDBのエントリを更新
        update_event(event_id, response)

        return {'statusCode': 200, 'body': json.dumps({'message': 'OK'})}

    except Exception as e:
        error_message = create_error_message("予期せぬエラー", str(e))
        logger.error(error_message)
        
        # エラーメッセージをSlackに送信
        try:
            channel_id = json.loads(event['body'])['event']['channel']
            thread_ts = json.loads(event['body'])['event'].get('thread_ts', json.loads(event['body'])['event']['ts'])
            send_slack_message(channel_id, error_message, thread_ts)
        except Exception as slack_error:
            logger.error(f"Slackへのエラーメッセージ送信中にエラーが発生しました：{str(slack_error)}")
        
        return {'statusCode': 500, 'body': json.dumps({'error': 'Internal Server Error'})}
