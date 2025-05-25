import os

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]

# AI モデル関連
AI_MODEL_MAX_TOKENS = 2048
AI_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"
AI_MODEL_VERSION = "bedrock-2023-05-31"
