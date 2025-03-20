import json
from unittest.mock import patch
from lambda_function import lambda_handler


@patch("lambda_function.handle_slack_event")
@patch("lambda_function.save_initial_event")
@patch("lambda_function.get_thread_history")
@patch("lambda_function.format_conversation_for_claude")
@patch("lambda_function.invoke_claude_model")
@patch("lambda_function.send_slack_message")
@patch("lambda_function.update_event")
def test_lambda_handler_success(
    mock_update_event,
    mock_send_slack_message,
    mock_invoke_claude_model,
    mock_format_conversation,
    mock_get_thread_history,
    mock_save_initial_event,
    mock_handle_slack_event,
):
    """lambda_handler関数が正常にイベントを処理できることをテスト"""
    # モックの設定
    mock_handle_slack_event.return_value = (
        "C123456",
        "U123456",
        "こんにちは",
        "1234567890.123456",
    )
    mock_save_initial_event.return_value = True
    mock_get_thread_history.return_value = [{"text": "過去のメッセージ"}]
    mock_format_conversation.return_value = (
        [{"role": "user", "content": "こんにちは"}],
        0,
    )
    mock_invoke_claude_model.return_value = "AIからの応答"

    # テストデータ
    event = {
        "body": json.dumps(
            {
                "event": {
                    "type": "app_mention",
                    "channel": "C123456",
                    "user": "U123456",
                    "text": "<@U123456> こんにちは",
                    "ts": "1234567890.123456",
                },
                "event_id": "Ev123456",
            }
        )
    }

    # 関数の実行
    response = lambda_handler(event, {})

    # 検証
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["message"] == "OK"
    mock_handle_slack_event.assert_called_once()
    mock_save_initial_event.assert_called_once_with(
        "Ev123456", "U123456", "C123456", "1234567890.123456", "こんにちは"
    )
    mock_get_thread_history.assert_called_once_with("C123456", "1234567890.123456")
    mock_format_conversation.assert_called_once()
    mock_invoke_claude_model.assert_called_once()
    mock_send_slack_message.assert_called_once_with(
        "C123456", "AIからの応答", "1234567890.123456"
    )
    mock_update_event.assert_called_once_with("Ev123456", "AIからの応答")


def test_lambda_handler_challenge():
    """lambda_handler関数がSlackのチャレンジリクエストを正しく処理できることをテスト"""
    # テストデータ
    event = {"body": json.dumps({"challenge": "test_challenge_token"})}

    # 関数の実行
    response = lambda_handler(event, {})

    # 検証
    assert response["statusCode"] == 200
    assert response["body"] == "test_challenge_token"


@patch("lambda_function.handle_slack_event")
@patch("lambda_function.save_initial_event")
def test_lambda_handler_duplicate_event(
    mock_save_initial_event, mock_handle_slack_event
):
    """lambda_handler関数が重複イベントを正しく処理できることをテスト"""
    # モックの設定
    mock_handle_slack_event.return_value = (
        "C123456",
        "U123456",
        "こんにちは",
        "1234567890.123456",
    )
    mock_save_initial_event.return_value = False  # 重複イベント

    # テストデータ
    event = {
        "body": json.dumps(
            {
                "event": {
                    "type": "app_mention",
                    "channel": "C123456",
                    "user": "U123456",
                    "text": "<@U123456> こんにちは",
                    "ts": "1234567890.123456",
                },
                "event_id": "Ev123456",
            }
        )
    }

    # 関数の実行
    response = lambda_handler(event, {})

    # 検証
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["message"] == "Duplicate event ignored"
    mock_save_initial_event.assert_called_once()


@patch("lambda_function.handle_slack_event")
@patch("lambda_function.save_initial_event")
@patch("lambda_function.get_thread_history")
@patch("lambda_function.format_conversation_for_claude")
@patch("lambda_function.send_slack_message")
def test_lambda_handler_response_limit(
    mock_send_slack_message,
    mock_format_conversation,
    mock_get_thread_history,
    mock_save_initial_event,
    mock_handle_slack_event,
):
    """lambda_handler関数がレスポンス数の制限を正しく処理できることをテスト"""
    # モックの設定
    mock_handle_slack_event.return_value = (
        "C123456",
        "U123456",
        "こんにちは",
        "1234567890.123456",
    )
    mock_save_initial_event.return_value = True
    mock_get_thread_history.return_value = [{"text": "過去のメッセージ"}]
    mock_format_conversation.return_value = (
        [{"role": "user", "content": "こんにちは"}],
        50,  # レスポンス数が制限に達した
    )

    # テストデータ
    event = {
        "body": json.dumps(
            {
                "event": {
                    "type": "app_mention",
                    "channel": "C123456",
                    "user": "U123456",
                    "text": "<@U123456> こんにちは",
                    "ts": "1234567890.123456",
                },
                "event_id": "Ev123456",
            }
        )
    }

    # 関数の実行
    response = lambda_handler(event, {})

    # 検証
    assert response["statusCode"] == 200
    mock_send_slack_message.assert_called_once()
    args, kwargs = mock_send_slack_message.call_args
    assert "制限を超えました" in args[1]  # 制限メッセージが含まれていることを確認


@patch("lambda_function.handle_slack_event")
@patch("lambda_function.save_initial_event")
@patch("lambda_function.get_thread_history")
@patch("lambda_function.extract_url")
@patch("lambda_function.get_url_content")
@patch("lambda_function.format_conversation_for_claude")
@patch("lambda_function.invoke_claude_model")
@patch("lambda_function.send_slack_message")
def test_lambda_handler_with_url(
    mock_send_slack_message,
    mock_invoke_claude_model,
    mock_format_conversation,
    mock_get_url_content,
    mock_extract_url,
    mock_get_thread_history,
    mock_save_initial_event,
    mock_handle_slack_event,
):
    """lambda_handler関数がURL付きメッセージを正しく処理できることをテスト"""
    # モックの設定
    mock_handle_slack_event.return_value = (
        "C123456",
        "U123456",
        "こちらのURLを確認してください <https://example.com|リンク>",
        "1234567890.123456",
    )
    mock_save_initial_event.return_value = True
    mock_get_thread_history.return_value = [{"text": "過去のメッセージ"}]
    mock_extract_url.return_value = "https://example.com"
    mock_get_url_content.return_value = ("Example Title", "Example Content")
    mock_format_conversation.return_value = (
        [{"role": "user", "content": "こちらのURLを確認してください"}],
        0,
    )
    mock_invoke_claude_model.return_value = "AIからの応答"

    # テストデータ
    event = {
        "body": json.dumps(
            {
                "event": {
                    "type": "app_mention",
                    "channel": "C123456",
                    "user": "U123456",
                    "text": "<@U123456> こちらのURLを確認してください <https://example.com|リンク>",
                    "ts": "1234567890.123456",
                },
                "event_id": "Ev123456",
            }
        )
    }

    # 関数の実行
    response = lambda_handler(event, {})

    # 検証
    assert response["statusCode"] == 200
    mock_extract_url.assert_called_once()
    mock_get_url_content.assert_called_once_with("https://example.com")
    mock_format_conversation.assert_called_once()
    # URLの内容がメッセージに追加されていることを確認
    args, kwargs = mock_format_conversation.call_args
    assert "Example Title" in args[1]
    assert "Example Content" in args[1]


@patch("lambda_function.handle_slack_event")
@patch("lambda_function.save_initial_event")
@patch("lambda_function.get_thread_history")
@patch("lambda_function.extract_url")
@patch("lambda_function.get_url_content")
@patch("lambda_function.format_conversation_for_claude")
@patch("lambda_function.invoke_claude_model")
@patch("lambda_function.send_slack_message")
def test_lambda_handler_with_url_only(
    mock_send_slack_message,
    mock_invoke_claude_model,
    mock_format_conversation,
    mock_get_url_content,
    mock_extract_url,
    mock_get_thread_history,
    mock_save_initial_event,
    mock_handle_slack_event,
):
    """lambda_handler関数がURLのみのメッセージを正しく処理できることをテスト"""
    # モックの設定
    mock_handle_slack_event.return_value = (
        "C123456",
        "U123456",
        "<https://example.com>",
        "1234567890.123456",
    )
    mock_save_initial_event.return_value = True
    mock_get_thread_history.return_value = [{"text": "過去のメッセージ"}]
    mock_extract_url.return_value = "https://example.com"
    mock_get_url_content.return_value = ("Example Title", "Example Content")
    mock_format_conversation.return_value = (
        [{"role": "user", "content": "<https://example.com>"}],
        0,
    )
    mock_invoke_claude_model.return_value = "AIからの応答"

    # テストデータ
    event = {
        "body": json.dumps(
            {
                "event": {
                    "type": "app_mention",
                    "channel": "C123456",
                    "user": "U123456",
                    "text": "<@U123456> <https://example.com>",
                    "ts": "1234567890.123456",
                },
                "event_id": "Ev123456",
            }
        )
    }

    # 関数の実行
    response = lambda_handler(event, {})

    # 検証
    assert response["statusCode"] == 200
    mock_extract_url.assert_called_once()
    mock_get_url_content.assert_called_once_with("https://example.com")
    mock_format_conversation.assert_called_once()
    # URLの内容が要約リクエストとして追加されていることを確認
    args, kwargs = mock_format_conversation.call_args
    assert "要約してください" in args[1]
    assert "Example Title" in args[1]
    assert "Example Content" in args[1]


@patch("lambda_function.handle_slack_event")
@patch("lambda_function.save_initial_event")
@patch("lambda_function.get_thread_history")
@patch("lambda_function.extract_url")
@patch("lambda_function.get_url_content")
@patch("lambda_function.format_conversation_for_claude")
@patch("lambda_function.invoke_claude_model")
@patch("lambda_function.send_slack_message")
def test_lambda_handler_with_url_error(
    mock_send_slack_message,
    mock_invoke_claude_model,
    mock_format_conversation,
    mock_get_url_content,
    mock_extract_url,
    mock_get_thread_history,
    mock_save_initial_event,
    mock_handle_slack_event,
):
    """lambda_handler関数がURL取得エラーを正しく処理できることをテスト"""
    # モックの設定
    mock_handle_slack_event.return_value = (
        "C123456",
        "U123456",
        "こちらのURLを確認してください <https://example.com|リンク>",
        "1234567890.123456",
    )
    mock_save_initial_event.return_value = True
    mock_get_thread_history.return_value = [{"text": "過去のメッセージ"}]
    mock_extract_url.return_value = "https://example.com"
    mock_get_url_content.side_effect = Exception("URL取得エラー")
    mock_format_conversation.return_value = (
        [{"role": "user", "content": "こちらのURLを確認してください"}],
        0,
    )
    mock_invoke_claude_model.return_value = "AIからの応答"

    # テストデータ
    event = {
        "body": json.dumps(
            {
                "event": {
                    "type": "app_mention",
                    "channel": "C123456",
                    "user": "U123456",
                    "text": "<@U123456> こちらのURLを確認してください <https://example.com|リンク>",
                    "ts": "1234567890.123456",
                },
                "event_id": "Ev123456",
            }
        )
    }

    # 関数の実行
    response = lambda_handler(event, {})

    # 検証
    assert response["statusCode"] == 200
    mock_extract_url.assert_called_once()
    mock_get_url_content.assert_called_once_with("https://example.com")
    mock_format_conversation.assert_called_once()
    # エラーメッセージがメッセージに追加されていることを確認
    args, kwargs = mock_format_conversation.call_args
    assert "URL内容取得を試みましたが、失敗しました" in args[1]
    assert "URL取得エラー" in args[1]


@patch("lambda_function.create_error_message")
@patch("lambda_function.send_slack_message")
def test_lambda_handler_general_error(
    mock_send_slack_message, mock_create_error_message
):
    """lambda_handler関数が一般的なエラーを正しく処理できることをテスト"""
    # モックの設定
    mock_create_error_message.return_value = "エラーメッセージ"

    # テストデータ - 不正なJSON形式
    event = {"body": "不正なJSON"}

    # 関数の実行
    response = lambda_handler(event, {})

    # 検証
    assert response["statusCode"] == 500
    assert json.loads(response["body"])["error"] == "Internal Server Error"
    mock_create_error_message.assert_called_once()
    # エラー時にSlackへのメッセージ送信を試みることを確認
    mock_send_slack_message.assert_not_called()  # この場合は呼ばれない（bodyが不正なため）


@patch("lambda_function.handle_slack_event", side_effect=Exception("テストエラー"))
@patch("lambda_function.create_error_message")
@patch("lambda_function.send_slack_message")
def test_lambda_handler_error_with_slack_message(
    mock_send_slack_message, mock_create_error_message, mock_handle_slack_event
):
    """lambda_handler関数がエラー時にSlackにメッセージを送信できることをテスト"""
    # モックの設定
    mock_create_error_message.return_value = "エラーメッセージ"

    # テストデータ
    event = {
        "body": json.dumps(
            {
                "event": {
                    "type": "app_mention",
                    "channel": "C123456",
                    "user": "U123456",
                    "text": "<@U123456> こんにちは",
                    "ts": "1234567890.123456",
                },
                "event_id": "Ev123456",
            }
        )
    }

    # 関数の実行
    response = lambda_handler(event, {})

    # 検証
    assert response["statusCode"] == 500
    assert json.loads(response["body"])["error"] == "Internal Server Error"
    mock_create_error_message.assert_called_once()
    mock_send_slack_message.assert_called_once_with(
        "C123456", "エラーメッセージ", "1234567890.123456"
    )


def test_lambda_handler_non_app_mention():
    """lambda_handler関数がapp_mention以外のイベントを正しく処理できることをテスト"""
    # テストデータ - message型のイベント
    event = {
        "body": json.dumps(
            {
                "event": {
                    "type": "message",
                    "channel": "C123456",
                    "user": "U123456",
                    "text": "通常のメッセージ",
                    "ts": "1234567890.123456",
                },
                "event_id": "Ev123456",
            }
        )
    }

    # 関数の実行
    response = lambda_handler(event, {})

    # 検証
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["message"] == "OK"
