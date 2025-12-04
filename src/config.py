import os

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]

# AI モデル関連
AI_MODEL_MAX_TOKENS = 2048
AI_MODEL_ID = "global.anthropic.claude-opus-4-5-20251101-v1:0"
AI_MODEL_VERSION = "bedrock-2023-05-31"
AI_SYSTEM_PROMPT = (
    "あなたはSlackチャットボットです。チャットの応答として自然な対話となるよう心がけてください。"
    "markdownや箇条書きだけで回答することは控え、要所でのみ活用するようにしましょう。"
)

# Slack 関連
SLACK_MESSAGE_LIMIT = 3000
