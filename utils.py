import re

def create_error_message(error_type, details):
    return f"申し訳ありません。{error_type}中にエラーが発生しました。詳細: {details}"

def extract_url(message):
    url_match = re.search(r'<(https?://[^|>]+)(?:\|[^>]+)?>', message)
    return url_match.group(1) if url_match else None
