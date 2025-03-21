#!/bin/bash

set -eu

VENV_PATH="exec_test"

# 仮想環境があるか確認し、なければ作成
if [ ! -d "$VENV_PATH" ]; then
    echo "仮想環境が見つかりません。新しく作成します: $VENV_PATH"
    python3 -m venv "$VENV_PATH"
    
    # 仮想環境を有効化して依存パッケージをインストール
    source "$VENV_PATH/bin/activate"
    pip install --upgrade pip
    pip install -r requirements.txt
    pip install -r requirements-dev.txt
    echo "必要なパッケージをインストールしました"
else
    echo "既存の仮想環境を使用します: $VENV_PATH"
    source "$VENV_PATH/bin/activate"
fi

# 必要な環境変数を設定（テスト用のダミー値）
export SLACK_BOT_TOKEN="xoxb-test-token"
export DYNAMODB_TABLE_NAME="test-table"

# pytestを実行
export PYTHONPATH="src"
python3 -m pytest "$@"

echo "テスト完了。仮想環境: $VENV_PATH"
