from unittest.mock import patch, MagicMock
import pytest
from slack_utils import (
    handle_slack_event,
    get_thread_history,
    send_slack_message,
    is_text_file,
    get_file_content,
    process_files,
    split_message,
    convert_markdown_to_slack_mrkdwn,
)


@patch("slack_utils.process_files")
@patch("slack_utils.get_bot_user_id", return_value="U123456")
def test_handle_slack_event_basic(mock_get_bot_user_id, mock_process_files):
    """handle_slack_event関数が基本的なSlackイベントを正しく処理できることをテスト"""
    # モックの設定
    mock_process_files.return_value = []

    # テストデータ
    slack_event = {
        "channel": "C123456",
        "user": "U123456",
        "text": "<@U123456> こんにちは",
        "ts": "1234567890.123456",
    }

    # 関数の実行
    channel_id, user_id, message, thread_ts = handle_slack_event(slack_event)

    # 検証
    assert channel_id == "C123456"
    assert user_id == "U123456"
    assert message == "こんにちは"
    assert thread_ts == "1234567890.123456"
    mock_process_files.assert_called_once_with([])


@patch("slack_utils.process_files")
@patch("slack_utils.get_bot_user_id", return_value="U123456")
def test_handle_slack_event_mention_after_message(
    mock_get_bot_user_id, mock_process_files
):
    """メッセージよりmentionが後ろにあってもhandle_slack_event関数が正しく処理できることをテスト"""
    # モックの設定
    mock_process_files.return_value = []

    # テストデータ
    slack_event = {
        "channel": "C123456",
        "user": "U123456",
        "text": "こんにちは <@U123456>",
        "ts": "1234567890.123456",
    }

    # 関数の実行
    channel_id, user_id, message, thread_ts = handle_slack_event(slack_event)

    # 検証
    assert channel_id == "C123456"
    assert user_id == "U123456"
    assert message == "こんにちは"
    assert thread_ts == "1234567890.123456"
    mock_process_files.assert_called_once_with([])


@patch("slack_utils.process_files")
@patch("slack_utils.get_bot_user_id", return_value="U123456")
def test_handle_slack_event_with_thread(mock_get_bot_user_id, mock_process_files):
    """handle_slack_event関数がスレッド内のイベントを正しく処理できることをテスト"""
    # モックの設定
    mock_process_files.return_value = []

    # テストデータ
    slack_event = {
        "channel": "C123456",
        "user": "U123456",
        "text": "<@U123456> スレッド内のメッセージ",
        "ts": "1234567890.123456",
        "thread_ts": "1234567890.000000",
    }

    # 関数の実行
    channel_id, user_id, message, thread_ts = handle_slack_event(slack_event)

    # 検証
    assert thread_ts == "1234567890.000000"  # thread_tsが優先されることを確認
    assert message == "スレッド内のメッセージ"


@patch("slack_utils.process_files")
@patch("slack_utils.get_bot_user_id", return_value="U123456")
def test_handle_slack_event_with_files(mock_get_bot_user_id, mock_process_files):
    """handle_slack_event関数がファイル添付を正しく処理できることをテスト"""
    # モックの設定
    mock_process_files.return_value = ["ファイル内容1", "ファイル内容2"]

    # テストデータ
    slack_event = {
        "channel": "C123456",
        "user": "U123456",
        "text": "<@U123456> ファイル添付あり",
        "ts": "1234567890.123456",
        "files": [{"id": "F123"}, {"id": "F456"}],
    }

    # 関数の実行
    _, _, message, _ = handle_slack_event(slack_event)

    # 検証
    assert "ファイル添付あり" in message
    assert "添付ファイルの内容" in message
    assert "ファイル内容1" in message
    assert "ファイル内容2" in message
    mock_process_files.assert_called_once_with([{"id": "F123"}, {"id": "F456"}])


@patch("slack_utils.slack_client.conversations_replies")
def test_get_thread_history_success(mock_conversations_replies):
    """get_thread_history関数がスレッド履歴を正しく取得できることをテスト"""
    # モックの設定
    mock_conversations_replies.return_value = {
        "messages": [
            {"ts": "1234567890.000000", "user": "U123", "text": "最初のメッセージ"},
            {"ts": "1234567890.000001", "user": "U456", "text": "返信1"},
        ]
    }

    # 関数の実行
    with patch("slack_utils.BOT_USER_ID", "U456"):
        with patch("slack_utils.extract_url", return_value=None):
            messages = get_thread_history("C123", "1234567890.000000")

    # 検証
    assert len(messages) == 2
    assert messages[0]["text"] == "最初のメッセージ"
    assert messages[1]["text"] == "返信1"
    mock_conversations_replies.assert_called_once_with(
        channel="C123", ts="1234567890.000000"
    )


@patch("slack_utils.slack_client.conversations_replies")
def test_get_thread_history_with_url(mock_conversations_replies):
    """get_thread_history関数がURL付きメッセージを正しく処理できることをテスト"""
    # モックの設定
    mock_conversations_replies.return_value = {
        "messages": [
            {
                "ts": "1234567890.000000",
                "user": "U123",
                "text": "URLあり <https://example.com|リンク>",
            }
        ]
    }

    # 関数の実行
    with patch("slack_utils.BOT_USER_ID", "U456"):
        with patch("slack_utils.extract_url", return_value="https://example.com"):
            with patch(
                "slack_utils.get_url_content",
                return_value=("Example Title", "Example Content"),
            ):
                messages = get_thread_history("C123", "1234567890.000000")

    # 検証
    assert len(messages) == 1
    assert "URLあり" in messages[0]["text"]
    assert "URLの内容" in messages[0]["text"]
    assert "Example Title" in messages[0]["text"]
    assert "Example Content" in messages[0]["text"]


@patch("slack_utils.slack_client.chat_postMessage")
def test_send_slack_message_success(mock_chat_post_message):
    """send_slack_message関数がメッセージを正しく送信できることをテスト"""
    # 関数の実行
    send_slack_message("C123", "テストメッセージ", "1234567890.123456")

    # 検証
    mock_chat_post_message.assert_called_once_with(
        channel="C123", text="テストメッセージ", thread_ts="1234567890.123456"
    )


@patch("slack_utils.slack_client.chat_postMessage")
def test_send_slack_message_error(mock_chat_post_message):
    """send_slack_message関数がエラー時に例外を発生させることをテスト"""
    # モックの設定
    mock_chat_post_message.side_effect = Exception("API error")

    # 関数の実行と例外の検証
    with pytest.raises(Exception):
        send_slack_message("C123", "テストメッセージ", "1234567890.123456")


def test_is_text_file():
    """is_text_file関数がテキストファイルを正しく識別できることをテスト"""
    # テキストファイル
    assert is_text_file({"mimetype": "text/plain"}) is True
    assert is_text_file({"filetype": "python"}) is True
    assert is_text_file({"filetype": "javascript"}) is True

    # 非テキストファイル
    assert is_text_file({"mimetype": "image/jpeg"}) is False
    assert is_text_file({"filetype": "jpg"}) is False
    assert is_text_file({}) is False


@patch("slack_utils.slack_client.files_info")
@patch("slack_utils.requests.get")
def test_get_file_content_success(mock_requests_get, mock_files_info):
    """get_file_content関数がファイル内容を正しく取得できることをテスト"""
    # モックの設定
    mock_files_info.return_value = {
        "file": {"url_private": "https://files.slack.com/file1"}
    }
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "ファイルの内容"
    mock_requests_get.return_value = mock_response

    # 関数の実行
    content = get_file_content("F123")

    # 検証
    assert content == "ファイルの内容"
    mock_files_info.assert_called_once_with(file="F123")
    mock_requests_get.assert_called_once()


@patch("slack_utils.get_file_content")
def test_process_files(mock_get_file_content):
    """process_files関数が複数のファイルを正しく処理できることをテスト"""
    # モックの設定
    mock_get_file_content.side_effect = ["ファイル1の内容", "ファイル2の内容"]

    # テストデータ
    files = [
        {"id": "F123", "name": "test1.txt", "mimetype": "text/plain"},
        {"id": "F456", "name": "test2.py", "filetype": "python"},
        {
            "id": "F789",
            "name": "image.jpg",
            "mimetype": "image/jpeg",
        },  # 非テキストファイル
    ]

    # 関数の実行
    result = process_files(files)

    # 検証
    assert len(result) == 2  # テキストファイルのみ処理される
    assert "ファイル名: test1.txt" in result[0]
    assert "ファイル1の内容" in result[0]
    assert "ファイル名: test2.py" in result[1]
    assert "ファイル2の内容" in result[1]
    assert mock_get_file_content.call_count == 2


def test_split_message_short():
    """split_message関数が短いメッセージをそのまま返すことをテスト"""
    text = "短いメッセージ"
    assert split_message(text) == ["短いメッセージ"]


def test_split_message_at_newline():
    """split_message関数が改行位置で分割することをテスト"""
    text = "a" * 2000 + "\n" + "b" * 2000
    result = split_message(text, limit=3000)
    assert len(result) == 2
    assert result[0] == "a" * 2000
    assert result[1] == "b" * 2000


def test_split_message_long():
    """split_message関数が長いメッセージを複数に分割することをテスト"""
    text = "a" * 10000
    result = split_message(text, limit=3000)
    assert len(result) == 4


@patch("slack_utils.slack_client.chat_postMessage")
def test_send_slack_message_splits_long_message(mock_chat_post_message):
    """send_slack_message関数が長いメッセージを分割して送信することをテスト"""
    text = "a" * 5000
    send_slack_message("C123", text, "123.456")
    assert mock_chat_post_message.call_count == 2


# convert_markdown_to_slack_mrkdwn テスト
def test_convert_bold():
    """ボールド(**text**)が正しく変換されることをテスト"""
    assert convert_markdown_to_slack_mrkdwn("**太字**") == "*太字*"
    assert convert_markdown_to_slack_mrkdwn("これは**太字**です") == "これは*太字*です"


def test_convert_italic():
    """イタリック(*text*)が正しく変換されることをテスト"""
    assert convert_markdown_to_slack_mrkdwn("*斜体*") == "_斜体_"
    assert convert_markdown_to_slack_mrkdwn("これは*斜体*です") == "これは_斜体_です"


def test_convert_bold_and_italic():
    """ボールドとイタリックが混在する場合に正しく変換されることをテスト"""
    assert convert_markdown_to_slack_mrkdwn("**太字**と*斜体*") == "*太字*と_斜体_"


def test_convert_strikethrough():
    """取り消し線(~~text~~)が正しく変換されることをテスト"""
    assert convert_markdown_to_slack_mrkdwn("~~削除~~") == "~削除~"
    assert convert_markdown_to_slack_mrkdwn("これは~~削除~~です") == "これは~削除~です"


def test_convert_header():
    """ヘッダー(### text)が太字に変換されることをテスト"""
    assert convert_markdown_to_slack_mrkdwn("### 見出し") == "*見出し*"
    assert convert_markdown_to_slack_mrkdwn("# H1見出し") == "*H1見出し*"
    assert convert_markdown_to_slack_mrkdwn("###### H6見出し") == "*H6見出し*"


def test_convert_header_multiline():
    """複数行のヘッダーが正しく変換されることをテスト"""
    input_text = "## 見出し1\n本文\n### 見出し2"
    expected = "*見出し1*\n本文\n*見出し2*"
    assert convert_markdown_to_slack_mrkdwn(input_text) == expected


def test_preserve_inline_code():
    """インラインコード内が変換されないことをテスト"""
    assert convert_markdown_to_slack_mrkdwn("`**code**`") == "`**code**`"
    assert convert_markdown_to_slack_mrkdwn("`*italic*`") == "`*italic*`"


def test_preserve_fenced_code_block():
    """フェンスドコードブロック内が変換されないことをテスト"""
    input_text = "```\n**bold** in code\n```"
    assert convert_markdown_to_slack_mrkdwn(input_text) == input_text


def test_preserve_code_with_surrounding_text():
    """コードブロック外のテキストのみ変換されることをテスト"""
    input_text = "**太字** `**コード内**` **また太字**"
    expected = "*太字* `**コード内**` *また太字*"
    assert convert_markdown_to_slack_mrkdwn(input_text) == expected


def test_convert_empty_string():
    """空文字列が正しく処理されることをテスト"""
    assert convert_markdown_to_slack_mrkdwn("") == ""


def test_convert_none():
    """Noneが正しく処理されることをテスト"""
    assert convert_markdown_to_slack_mrkdwn(None) is None


def test_convert_no_conversion_needed():
    """変換対象がない場合にテキストがそのまま返されることをテスト"""
    text = "普通のテキスト"
    assert convert_markdown_to_slack_mrkdwn(text) == text


def test_convert_links_unchanged():
    """Markdownリンクが変更されないことをテスト"""
    text = "[リンクテキスト](https://example.com)"
    assert convert_markdown_to_slack_mrkdwn(text) == text


def test_convert_blockquotes_unchanged():
    """引用が変更されないことをテスト"""
    text = "> これは引用です"
    assert convert_markdown_to_slack_mrkdwn(text) == text


def test_convert_lists_unchanged():
    """リストが変更されないことをテスト"""
    text = "- item1\n- item2"
    assert convert_markdown_to_slack_mrkdwn(text) == text


def test_convert_real_world_example():
    """実際のClaudeレスポンス例が正しく変換されることをテスト"""
    input_text = (
        "**打開のポイントは「受け身から主導権を取り戻す」こと**\n\n具体的には："
    )
    expected = "*打開のポイントは「受け身から主導権を取り戻す」こと*\n\n具体的には："
    assert convert_markdown_to_slack_mrkdwn(input_text) == expected


def test_convert_complex_example():
    """複雑なMarkdownが正しく変換されることをテスト"""
    input_text = """## まとめ

**重要なポイント**は以下の通りです：

1. *イタリック*のテキスト
2. ~~取り消し線~~のテキスト
3. `コード`は変換されない

```python
# **コメント**も変換されない
print("hello")
```"""
    expected = """*まとめ*

*重要なポイント*は以下の通りです：

1. _イタリック_のテキスト
2. ~取り消し線~のテキスト
3. `コード`は変換されない

```python
# **コメント**も変換されない
print("hello")
```"""
    assert convert_markdown_to_slack_mrkdwn(input_text) == expected
