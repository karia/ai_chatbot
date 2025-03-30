import json
import logging
from slack_utils import handle_slack_event, send_slack_message, get_thread_history
from dynamodb_utils import save_initial_event, update_event
from bedrock_utils import invoke_claude_model, format_conversation_for_claude
from url_utils import get_url_content
from utils import create_error_message, extract_url

# ロガーの設定
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def validate_and_parse_event(event):
    """
    Slackイベントの検証とパース処理を行う

    Args:
        event: Lambda関数に渡されるイベント

    Returns:
        tuple: (channel_id, user_id, message, thread_ts, event_id) または
               challenge responseの場合はそのレスポンス
    """
    # Slack Event APIからのチャレンジレスポンスの処理
    if "challenge" in event["body"]:
        return {"statusCode": 200, "body": json.loads(event["body"])["challenge"]}

    # イベントの解析
    body = json.loads(event["body"])
    slack_event = body["event"]
    event_id = body["event_id"]

    # デバッグ: パースされたbodyをログに出力
    logger.info(f"Parsed body: {json.dumps(body, indent=2)}")

    # app_mentionイベント以外は無視
    if slack_event["type"] != "app_mention":
        return {"statusCode": 200, "body": json.dumps({"message": "OK"})}

    # Slackイベントの処理
    channel_id, user_id, message, thread_ts = handle_slack_event(slack_event)

    return channel_id, user_id, message, thread_ts, event_id


def process_url_content(message):
    """
    メッセージからURLを抽出し、内容を取得する

    Args:
        message: ユーザーメッセージ

    Returns:
        str: 処理後のメッセージ
    """
    # URLの抽出
    url = extract_url(message)
    processed_message = message

    if url:
        try:
            url_title, url_content = get_url_content(url)

            # URLのみの場合とそうでない場合で処理を分ける
            if message.strip() == f"<{url}>":
                processed_message += (
                    f"\n\n上記URLのウェブページの内容を以下に示しますので、簡潔に要約してください。"
                    f"要約の冒頭に「ウェブページの要約は以下の通りです：」と1行追加してください。：\n\n"
                    f"タイトル: {url_title}\n"
                    f"本文: {url_content}\n"
                )
            else:
                processed_message += (
                    f"\n\nURLの内容：\n\nタイトル:{url_title}\n本文:{url_content}"
                )
        except Exception as e:
            logger.error(f"Error processing URL {url}: {str(e)}")
            processed_message += (
                f"【システムメッセージ】URL内容取得を試みましたが、失敗しました。\n"
                f"対象URL: {url}\n"
                f"エラーメッセージ: {str(e)}"
            )

    return processed_message


def process_conversation(conversation_history, message):
    """
    会話履歴とメッセージをモデルに送信し、レスポンスを取得する

    Args:
        conversation_history: 会話履歴
        message: 処理済みメッセージ

    Returns:
        str: AIの応答
    """
    messages, assistant_response_count = format_conversation_for_claude(
        conversation_history, message
    )

    if assistant_response_count >= 50:
        return "申し訳ありませんが、このスレッドでの回答回数が制限を超えました。新しいスレッドで質問していただくようお願いいたします。"
    else:
        ai_response = invoke_claude_model(messages)
        return ai_response


def handle_response(channel_id, thread_ts, response, event_id):
    """
    レスポンスをSlackに送信し、DynamoDBを更新する

    Args:
        channel_id: Slackチャンネルid
        thread_ts: スレッドts
        response: 送信するレスポンス
        event_id: イベントID
    """
    # AIの応答をログに記録
    logger.info(f"AI response: {response}")

    # Slackにメッセージを送信（スレッド内）
    send_slack_message(channel_id, response, thread_ts)

    # DynamoDBのエントリを更新
    update_event(event_id, response)


def handle_error(event, error):
    """
    エラーハンドリングを行う

    Args:
        event: Lambda関数に渡されるイベント
        error: 発生した例外

    Returns:
        dict: エラーレスポンス
    """
    error_message = create_error_message("処理中", str(error))
    logger.error(error_message)

    # エラーメッセージをSlackに送信
    try:
        channel_id = json.loads(event["body"])["event"]["channel"]
        thread_ts = json.loads(event["body"])["event"].get(
            "thread_ts", json.loads(event["body"])["event"]["ts"]
        )
        send_slack_message(channel_id, error_message, thread_ts)
    except Exception as slack_error:
        logger.error(
            f"Slackへのエラーメッセージ送信中にエラーが発生しました：{str(slack_error)}"
        )

    return {
        "statusCode": 500,
        "body": json.dumps({"error": "Internal Server Error"}),
    }


def lambda_handler(event, context):
    try:
        # デバッグ: リクエスト全体をログに出力
        logger.info(f"Received event: {json.dumps(event, indent=2)}")

        # イベント検証と解析
        result = validate_and_parse_event(event)

        # チャレンジレスポンスまたは無視すべきイベントの場合は早期リターン
        if isinstance(result, dict):
            return result

        channel_id, user_id, message, thread_ts, event_id = result

        # 最新のユーザーメッセージをログに記録
        logger.info(f"User message: {message}")

        # 仮のエントリをDynamoDBに保存
        if not save_initial_event(event_id, user_id, channel_id, thread_ts, message):
            logger.info(f"Duplicate event detected: {event_id}")
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "Duplicate event ignored"}),
            }

        # スレッドの会話履歴を取得
        conversation_history = get_thread_history(channel_id, thread_ts)

        # デバッグ: スレッドの内容をログに出力
        logger.info(f"Thread history: {json.dumps(conversation_history, indent=2)}")

        # 最新のメッセージを除外（handle_slack_eventで既に処理済み）
        conversation_history = conversation_history[:-1]

        # URLを処理したメッセージを取得
        processed_message = process_url_content(message)

        # 会話処理とAIレスポンスの取得
        response = process_conversation(conversation_history, processed_message)

        # レスポンス処理
        handle_response(channel_id, thread_ts, response, event_id)

        return {"statusCode": 200, "body": json.dumps({"message": "OK"})}

    except Exception as e:
        return handle_error(event, e)
