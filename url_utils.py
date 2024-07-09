import requests
from bs4 import BeautifulSoup
import re
import logging

logger = logging.getLogger()

def get_url_content(url):
    try:
        # URLから余分な文字（< >）を削除
        url = url.strip('<>')
        logger.info(f"Attempting to fetch content from URL: {url}")
        
        response = requests.get(url)
        logger.info(f"Response status code: {response.status_code}")
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # メタデータの取得
        title = soup.title.string if soup.title else "No title found"
        logger.info(f"Title: {title}")
        
        # body タグ内の全てのテキストを取得
        body = soup.body
        if body:
            # スクリプトとスタイルタグを除去
            for script_or_style in body(["script", "style"]):
                script_or_style.decompose()
            content = body.get_text()
        else:
            content = "No body content found"
        
        # 不要な空白文字の削除
        content = re.sub(r'\s+', ' ', content).strip()
        
        logger.info(f"Content length: {len(content)} characters")
        logger.info(f"Content preview: {content[:200]}...")
        
        return title, content
    except requests.RequestException as e:
        error_message = f"Error fetching URL content: {str(e)}"
        logger.error(error_message)
        return "Error", error_message
    except Exception as e:
        error_message = f"Unexpected error while fetching URL content: {str(e)}"
        logger.error(error_message)
        return "Error", error_message

def prepare_summary_prompt(url_content):
    title, content = url_content
    
    # プロンプトの最大長を設定（例：8000文字）
    max_prompt_length = 8000
    
    if len(content) > max_prompt_length - len(title) - 100:  # 100は余裕を持たせる
        content = content[:max_prompt_length - len(title) - 100] + "..."
    
    return f"以下のウェブページの内容を簡潔に要約してください：\n\nタイトル：{title}\n\n内容：{content}"
