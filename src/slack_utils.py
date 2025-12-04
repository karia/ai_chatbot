import logging
import re
import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from config import SLACK_BOT_TOKEN, SLACK_MESSAGE_LIMIT
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


def convert_markdown_to_slack_mrkdwn(text):
    """
    標準MarkdownをSlack mrkdwn形式に変換する

    変換内容:
    - **bold** → *bold*
    - *italic* → _italic_
    - ~~strikethrough~~ → ~strikethrough~
    - ### Header → *Header* (ヘッダーを太字に)

    コードブロック内は変換しない

    Args:
        text: 標準Markdown形式のテキスト

    Returns:
        Slack mrkdwn形式に変換されたテキスト
    """
    if not text:
        return text

    # コードブロックを一時的に保護
    code_blocks = []
    inline_codes = []

    def preserve_fenced_code(match):
        code_blocks.append(match.group(0))
        return f"\x00CODEBLOCK{len(code_blocks)-1}\x00"

    def preserve_inline_code(match):
        inline_codes.append(match.group(0))
        return f"\x00INLINECODE{len(inline_codes)-1}\x00"

    # フェンスドコードブロック → インラインコード の順で保護
    text = re.sub(r"```[\s\S]*?```", preserve_fenced_code, text)
    text = re.sub(r"`[^`]+`", preserve_inline_code, text)

    # Markdown変換
    # ボールド: **text** → 一時的にプレースホルダーに変換
    bold_texts = []

    def preserve_bold(match):
        bold_texts.append(match.group(1))
        return f"\x00BOLD{len(bold_texts)-1}\x00"

    text = re.sub(r"\*\*([^*]+)\*\*", preserve_bold, text)

    # イタリック: *text* → _text_ (ボールド変換後に実行)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"_\1_", text)

    # ボールドを復元: プレースホルダー → *text* (Slack mrkdwn形式)
    for i, bold_text in enumerate(bold_texts):
        text = text.replace(f"\x00BOLD{i}\x00", f"*{bold_text}*")

    # 取り消し線: ~~text~~ → ~text~
    text = re.sub(r"~~([^~]+)~~", r"~\1~", text)

    # ヘッダー: ### Header → *Header*
    text = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", text, flags=re.MULTILINE)

    # コードブロックを復元
    for i, block in enumerate(code_blocks):
        text = text.replace(f"\x00CODEBLOCK{i}\x00", block)
    for i, code in enumerate(inline_codes):
        text = text.replace(f"\x00INLINECODE{i}\x00", code)

    return text


def split_message(text, limit=SLACK_MESSAGE_LIMIT):
    """メッセージを指定文字数で分割（改行位置を考慮）"""
    if len(text) <= limit:
        return [text]

    messages = []
    while text:
        if len(text) <= limit:
            messages.append(text)
            break

        # 改行位置で分割を試みる
        split_pos = text.rfind("\n", 0, limit)
        if split_pos == -1 or split_pos < limit // 2:
            # 改行がない場合や位置が前すぎる場合はスペースで分割
            split_pos = text.rfind(" ", 0, limit)
        if split_pos == -1:
            split_pos = limit

        messages.append(text[:split_pos])
        text = text[split_pos:].lstrip()

    return messages


def send_slack_message(channel_id, text, thread_ts):
    try:
        converted_text = convert_markdown_to_slack_mrkdwn(text)
        messages = split_message(converted_text)
        for msg in messages:
            slack_client.chat_postMessage(
                channel=channel_id, text=msg, thread_ts=thread_ts
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
