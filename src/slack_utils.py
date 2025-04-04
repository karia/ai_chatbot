import logging
import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from config import SLACK_BOT_TOKEN
from utils import extract_url
from url_utils import get_url_content

logger = logging.getLogger()
slack_client = WebClient(token=SLACK_BOT_TOKEN)


def handle_slack_event(slack_event):
    channel_id = slack_event["channel"]
    user_id = slack_event["user"]
    message = slack_event["text"]
    thread_ts = slack_event.get("thread_ts", slack_event["ts"])

    # メッセージからボットメンションを除去
    # 正規表現を使うのがベストですが、シンプルなアプローチとして
    # メンション形式の <@Uxxxxxx> を検索して削除します
    bot_user_id = get_bot_user_id()
    if bot_user_id:
        bot_mention = f"<@{bot_user_id}>"
        message = message.replace(bot_mention, "").strip()

    # ファイルが添付されているか確認
    files = slack_event.get("files", [])
    file_contents = process_files(files)

    # ファイルの内容をメッセージに追加
    if file_contents:
        message += "\n\n添付ファイルの内容:\n" + "\n---\n".join(file_contents)

    return channel_id, user_id, message, thread_ts


def get_bot_user_id():
    try:
        response = slack_client.auth_test()
        return response["user_id"]
    except SlackApiError as e:
        logger.error(f"Error getting bot user ID: {e}")
        return None


BOT_USER_ID = get_bot_user_id()


def get_thread_history(channel_id, thread_ts):
    try:
        response = slack_client.conversations_replies(channel=channel_id, ts=thread_ts)
        messages = response["messages"]

        # 各メッセージの添付ファイル、URLを本文中に展開
        for msg in messages:
            # ボットのメッセージの場合はスキップ
            if msg.get("user") == BOT_USER_ID:
                continue

            # 添付ファイルを取得
            files = msg.get("files", [])
            file_contents = process_files(files)
            if file_contents:
                msg["text"] += "\n\n添付ファイルの内容:\n" + "\n---\n".join(
                    file_contents
                )

            # URLを取得
            url = extract_url(msg["text"])
            if url:
                try:
                    url_title, url_content = get_url_content(url)
                    msg[
                        "text"
                    ] += f"\n\nURLの内容：\n\nタイトル:{url_title}\n本文:{url_content}"
                except Exception as e:
                    logger.error(f"Error processing URL {url}: {str(e)}")
                    msg["text"] += (
                        f"\n\n【システムメッセージ】URL内容取得を試みましたが、失敗しました。\n"
                        f"対象URL: {url}\n"
                        f"エラーメッセージ: {str(e)}\n"
                    )

        return messages

    except SlackApiError as e:
        logger.error(f"Error fetching thread history: {e}")
        return []


def send_slack_message(channel_id, text, thread_ts):
    try:
        slack_client.chat_postMessage(
            channel=channel_id, text=text, thread_ts=thread_ts
        )
    except SlackApiError as e:
        logger.error(f"Error sending message to Slack: {e}")
        raise


def is_text_file(file):
    mimetype = file.get("mimetype", "")
    filetype = file.get("filetype", "")

    return mimetype.startswith("text/") or filetype in [
        "text",
        "python",
        "javascript",
        "java",
        "c",
        "cpp",
        "css",
        "html",
        "xml",
        "json",
        "yaml",
        "markdown",
        "plain_text",
    ]


def get_file_content(file_id):
    try:
        response = slack_client.files_info(file=file_id)
        file_url = response["file"]["url_private"]

        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
        content_response = requests.get(file_url, headers=headers)

        if content_response.status_code == 200:
            return content_response.text
        else:
            logger.error(
                "Failed to fetch file content. "
                f"Status code: {content_response.status_code}"
            )
            return None
    except SlackApiError as e:
        logger.error(f"Error fetching file content: {e}")
        return None


def process_files(files):
    file_contents = []
    for file in files:
        if is_text_file(file):
            content = get_file_content(file["id"])
            if content:
                file_contents.append(f"ファイル名: {file['name']}\n内容:\n{content}")
    return file_contents
