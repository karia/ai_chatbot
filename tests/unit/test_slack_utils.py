from unittest.mock import patch, MagicMock
import pytest
from slack_utils import (
    handle_slack_event,
    get_thread_history,
    send_slack_message,
    is_text_file,
    get_file_content,
    process_files,
)


@patch("slack_utils.process_files")
def test_handle_slack_event_basic(mock_process_files):
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
def test_handle_slack_event_with_thread(mock_process_files):
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
def test_handle_slack_event_with_files(mock_process_files):
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
