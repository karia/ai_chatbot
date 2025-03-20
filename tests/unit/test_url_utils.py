from unittest.mock import patch, MagicMock
from url_utils import get_url_content


@patch("url_utils.requests.get")
def test_get_url_content_success(mock_get):
    """get_url_content関数が正常にURLの内容を取得できることをテスト"""
    # モックレスポンスの設定
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = """
    <html>
        <head>
            <title>テストページ</title>
        </head>
        <body>
            <p>これはテストコンテンツです。</p>
        </body>
    </html>
    """
    mock_get.return_value = mock_response

    # 関数の実行
    title, content = get_url_content("https://example.com")

    # 検証
    mock_get.assert_called_once_with("https://example.com")
    assert title == "テストページ"
    assert "これはテストコンテンツです。" in content


@patch("url_utils.requests.get")
def test_get_url_content_with_angle_brackets(mock_get):
    """get_url_content関数が<>で囲まれたURLを正しく処理できることをテスト"""
    # モックレスポンスの設定
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = (
        "<html><head><title>Test</title></head><body>Test</body></html>"
    )
    mock_get.return_value = mock_response

    # 関数の実行
    title, content = get_url_content("<https://example.com>")

    # 検証
    mock_get.assert_called_once_with("https://example.com")
    assert title == "Test"
    assert "Test" in content


@patch("url_utils.requests.get")
def test_get_url_content_request_exception(mock_get):
    """get_url_content関数がリクエスト例外を適切に処理できることをテスト"""
    # リクエスト例外をシミュレート
    mock_get.side_effect = Exception("Connection error")

    # 関数の実行
    title, content = get_url_content("https://example.com")

    # 検証
    assert title == "Error"
    assert "Connection error" in content


@patch("url_utils.requests.get")
def test_get_url_content_no_title(mock_get):
    """get_url_content関数がタイトルのないHTMLを適切に処理できることをテスト"""
    # モックレスポンスの設定
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = "<html><body>No title content</body></html>"
    mock_get.return_value = mock_response

    # 関数の実行
    title, content = get_url_content("https://example.com")

    # 検証
    assert title == "No title found"
    assert "No title content" in content
