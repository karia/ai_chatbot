import json
import os
import pytest
from unittest.mock import MagicMock


# テスト用の環境変数設定
@pytest.fixture(autouse=True)
def setup_test_environment():
    """テスト実行時に必要な環境変数を自動的に設定する"""
    os.environ["SLACK_BOT_TOKEN"] = "test-slack-token"
    os.environ["DYNAMODB_TABLE_NAME"] = "test-table"
    # 必要に応じて他の環境変数を追加


# Slackイベントのフィクスチャ
@pytest.fixture
def slack_event_fixture():
    """Slackのapp_mentionイベントのテスト用フィクスチャ"""
    return {
        "type": "app_mention",
        "user": "U123456",
        "channel": "C123456",
        "ts": "1234567890.123456",
        "text": "<@U123456> こんにちは",
        "thread_ts": "1234567890.123456",
    }


# AWS Bedrockのモック
@pytest.fixture
def mock_bedrock_response():
    """AWS Bedrock APIレスポンスのモック"""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(
        {"content": [{"text": "これはテスト応答です"}]}
    )
    return mock_response


# DynamoDBのモック設定
@pytest.fixture
def aws_credentials():
    """テスト用のAWS認証情報を設定"""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
