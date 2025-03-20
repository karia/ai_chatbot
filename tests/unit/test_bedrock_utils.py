from unittest.mock import patch
from bedrock_utils import invoke_claude_model, format_conversation_for_claude


@patch("bedrock_utils.bedrock_runtime.invoke_model")
def test_invoke_claude_model_success(mock_invoke_model, mock_bedrock_response):
    """invoke_claude_model関数が正常にAIモデルを呼び出せることをテスト"""
    # モックレスポンスの設定
    mock_invoke_model.return_value = {"body": mock_bedrock_response}

    # 関数の実行
    messages = [{"role": "user", "content": "こんにちは"}]
    result = invoke_claude_model(messages)

    # 検証
    mock_invoke_model.assert_called_once()
    assert result == "これはテスト応答です"


@patch("bedrock_utils.bedrock_runtime.invoke_model")
def test_invoke_claude_model_error(mock_invoke_model):
    """invoke_claude_model関数がエラー時に例外を発生させることをテスト"""
    # エラーをシミュレート
    mock_invoke_model.side_effect = Exception("API error")

    # 関数の実行と例外の検証
    messages = [{"role": "user", "content": "こんにちは"}]
    try:
        invoke_claude_model(messages)
        assert False, "例外が発生しませんでした"
    except Exception as e:
        assert "Failed to invoke Bedrock model" in str(e)


def test_format_conversation_for_claude_empty():
    """format_conversation_for_claude関数が空の会話履歴を正しく処理できることをテスト"""
    conversation_history = []
    messages, count = format_conversation_for_claude(conversation_history)

    assert messages == []
    assert count == 0


def test_format_conversation_for_claude_with_history():
    """format_conversation_for_claude関数が会話履歴を正しくフォーマットできることをテスト"""
    conversation_history = [
        {"bot_id": None, "text": "<@U123> こんにちは"},
        {"bot_id": "B123", "text": "こんにちは、何かお手伝いできますか？"},
        {"bot_id": None, "text": "天気について教えて"},
    ]

    messages, count = format_conversation_for_claude(conversation_history)

    assert len(messages) == 3
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "こんにちは"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "こんにちは、何かお手伝いできますか？"
    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "天気について教えて"
    assert count == 1


def test_format_conversation_for_claude_with_append_message():
    """format_conversation_for_claude関数が追加メッセージを正しく処理できることをテスト"""
    conversation_history = [
        {"bot_id": None, "text": "<@U123> こんにちは"},
        {"bot_id": "B123", "text": "こんにちは、何かお手伝いできますか？"},
    ]
    append_message = "追加の質問があります"

    messages, count = format_conversation_for_claude(
        conversation_history, append_message
    )

    assert len(messages) == 3
    assert messages[2]["role"] == "user"
    assert messages[2]["content"] == "追加の質問があります"
    assert count == 1


def test_format_conversation_for_claude_consecutive_same_role():
    """format_conversation_for_claude関数が同じロールの連続メッセージを結合できることをテスト"""
    conversation_history = [
        {"bot_id": None, "text": "<@U123> 最初の質問"},
        {"bot_id": None, "text": "追加の質問"},
        {"bot_id": "B123", "text": "回答1"},
        {"bot_id": "B123", "text": "回答2"},
    ]

    messages, count = format_conversation_for_claude(conversation_history)

    assert len(messages) == 2  # 同じロールのメッセージが結合されるため2つになる
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "最初の質問\n追加の質問"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "回答1\n回答2"
    assert count == 2  # assistantのメッセージが2つ
