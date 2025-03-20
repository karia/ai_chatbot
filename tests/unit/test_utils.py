from utils import create_error_message, extract_url


def test_create_error_message():
    """create_error_message関数が正しいエラーメッセージを生成することをテスト"""
    result = create_error_message("テスト", "詳細情報")
    assert result == "申し訳ありません。テスト中にエラーが発生しました。詳細: 詳細情報"


def test_extract_url_with_url():
    """extract_url関数がSlackメッセージ形式のURLを正しく抽出することをテスト"""
    message = "こちらのリンクを確認してください <https://example.com|Example>"
    assert extract_url(message) == "https://example.com"


def test_extract_url_without_url():
    """extract_url関数がURLを含まないメッセージに対してNoneを返すことをテスト"""
    message = "URLはありません"
    assert extract_url(message) is None


def test_extract_url_with_multiple_urls():
    """extract_url関数が複数のURLを含むメッセージから最初のURLを抽出することをテスト"""
    message = "最初のリンク <https://example1.com|Example1> と2つ目のリンク <https://example2.com>"
    assert extract_url(message) == "https://example1.com"


def test_extract_url_with_url_without_label():
    """extract_url関数がラベルのないURLを正しく抽出することをテスト"""
    message = "こちらのリンクを確認してください <https://example.com>"
    assert extract_url(message) == "https://example.com"
