#!/bin/bash

# 必要な環境変数を設定（テスト用のダミー値）
export SLACK_BOT_TOKEN="xoxb-test-token"
export DYNAMODB_TABLE_NAME="test-table"

# pytestを実行
python -m pytest "$@"
