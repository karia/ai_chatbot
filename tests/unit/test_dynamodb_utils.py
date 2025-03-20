from unittest.mock import patch
import pytest
from botocore.exceptions import ClientError
from dynamodb_utils import save_initial_event, update_event


@patch("dynamodb_utils.table.put_item")
def test_save_initial_event_success(mock_put_item):
    """save_initial_event関数が正常にデータを保存できることをテスト"""
    # 関数の実行
    result = save_initial_event(
        "event123", "user123", "channel123", "1234567890.123456", "テストメッセージ"
    )

    # 検証
    assert result is True
    mock_put_item.assert_called_once()
    args, kwargs = mock_put_item.call_args
    assert kwargs["Item"]["event_id"] == "event123"
    assert kwargs["Item"]["user_id"] == "user123"
    assert kwargs["Item"]["channel_id"] == "channel123"
    assert kwargs["Item"]["thread_ts"] == "1234567890.123456"
    assert kwargs["Item"]["user_message"] == "テストメッセージ"
    assert kwargs["Item"]["status"] == "processing"
    assert "timestamp" in kwargs["Item"]
    assert "ConditionExpression" in kwargs


@patch("dynamodb_utils.table.put_item")
def test_save_initial_event_duplicate(mock_put_item):
    """save_initial_event関数が重複イベントを正しく処理できることをテスト"""
    # モックの設定
    error_response = {"Error": {"Code": "ConditionalCheckFailedException"}}
    mock_put_item.side_effect = ClientError(error_response, "PutItem")

    # 関数の実行
    result = save_initial_event(
        "event123", "user123", "channel123", "1234567890.123456", "テストメッセージ"
    )

    # 検証
    assert result is False
    mock_put_item.assert_called_once()


@patch("dynamodb_utils.table.put_item")
def test_save_initial_event_other_error(mock_put_item):
    """save_initial_event関数が他のエラーを正しく処理できることをテスト"""
    # モックの設定
    error_response = {"Error": {"Code": "InternalServerError"}}
    mock_put_item.side_effect = ClientError(error_response, "PutItem")

    # 関数の実行と例外の検証
    with pytest.raises(ClientError):
        save_initial_event(
            "event123", "user123", "channel123", "1234567890.123456", "テストメッセージ"
        )


@patch("dynamodb_utils.table.update_item")
def test_update_event_success(mock_update_item):
    """update_event関数が正常にデータを更新できることをテスト"""
    # 関数の実行
    update_event("event123", "AIからの応答")

    # 検証
    mock_update_item.assert_called_once()
    args, kwargs = mock_update_item.call_args
    assert kwargs["Key"]["event_id"] == "event123"
    assert "UpdateExpression" in kwargs
    assert "ExpressionAttributeValues" in kwargs
    assert kwargs["ExpressionAttributeValues"][":r"] == "AIからの応答"
    assert kwargs["ExpressionAttributeValues"][":c"] == "completed"


@patch("dynamodb_utils.table.update_item")
def test_update_event_already_processed(mock_update_item):
    """update_event関数が既に処理済みのイベントを正しく処理できることをテスト"""
    # モックの設定
    error_response = {"Error": {"Code": "ConditionalCheckFailedException"}}
    mock_update_item.side_effect = ClientError(error_response, "UpdateItem")

    # 関数の実行（例外が発生しないことを確認）
    update_event("event123", "AIからの応答")

    # 検証
    mock_update_item.assert_called_once()


@patch("dynamodb_utils.table.update_item")
def test_update_event_other_error(mock_update_item):
    """update_event関数が他のエラーを正しく処理できることをテスト"""
    # モックの設定
    error_response = {"Error": {"Code": "InternalServerError"}}
    mock_update_item.side_effect = ClientError(error_response, "UpdateItem")

    # 関数の実行と例外の検証
    with pytest.raises(ClientError):
        update_event("event123", "AIからの応答")


@patch("dynamodb_utils.time.time")
@patch("dynamodb_utils.table.put_item")
def test_save_initial_event_timestamp(mock_put_item, mock_time):
    """save_initial_event関数がタイムスタンプを正しく設定できることをテスト"""
    # モックの設定
    mock_time.return_value = 1234567.89

    # 関数の実行
    save_initial_event(
        "event123", "user123", "channel123", "1234567890.123456", "テストメッセージ"
    )

    # 検証
    args, kwargs = mock_put_item.call_args
    assert kwargs["Item"]["timestamp"] == 1234567890  # 1000倍されることを確認
