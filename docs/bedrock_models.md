# Amazon Bedrock モデル更新ガイド

## Bedrock モデル ID の命名規則

Anthropic モデルの Inference Profile ID は以下の形式：

```
{scope}.anthropic.{model-name}
```

- `scope`: `global`（全リージョン横断）または `jp`（東京・大阪リージョン）
- このチャットボットでは `global.` プレフィックスを使用

> **注意:** Opus 4.5 以前は `global.anthropic.claude-opus-4-5-20251101-v1:0` のように日付・バージョンサフィックスが付いていたが、Opus 4.7 以降は `global.anthropic.claude-opus-4-7` のようにシンプルな形式になった。

## モデル更新手順

### 1. 利用可能なモデル ID を確認

```bash
aws bedrock list-inference-profiles --region ap-northeast-1 | grep -i "claude"
```

`global.` プレフィックスの `inferenceProfileId` を使用する。

### 2. モデルが呼び出し可能か確認

```python
import boto3, json

client = boto3.client('bedrock-runtime', region_name='ap-northeast-1')
response = client.invoke_model(
    modelId='global.anthropic.claude-opus-4-7',  # 確認したいモデル ID
    body=json.dumps({
        'anthropic_version': 'bedrock-2023-05-31',
        'max_tokens': 10,
        'messages': [{'role': 'user', 'content': 'Hi'}]
    })
)
print(json.loads(response['body'].read()))
```

レスポンスが正常に返れば利用可能。

### 3. `src/config.py` を更新

```python
AI_MODEL_ID = "global.anthropic.claude-opus-4-7"  # 確認したモデル ID に変更
```

### 4. テスト実行

```bash
bash run_tests.sh
```

全テストが PASS することを確認する。

### 5. コミット & PR 作成

```bash
git checkout -b update/claude-opus-4-x
git add src/config.py
git commit -m "Update AI model to Claude Opus 4.x"
gh pr create --title "Update AI model to Claude Opus 4.x" --body "..."
```

> **注意:** `git -C <path>` や `cd <path> && git ...` のようなパス指定は使用しないこと（CLAUDE.md 参照）。作業前に `pwd` で CWD を確認してから素の git コマンドを実行すること。

### 6. デプロイ

リポジトリルートにある `function-*.json` を全てデプロイする：

```bash
for f in function-*.json; do
  bash deploy.sh "$f"
done
```

## トラブルシューティング

### AccessDeniedException: INVALID_PAYMENT_INSTRUMENT

新しいモデルを初めて使用する際に以下のエラーが出る場合がある：

```
An error occurred (AccessDeniedException) when calling the InvokeModel operation:
Model access is denied due to INVALID_PAYMENT_INSTRUMENT
```

**原因:** AWS アカウントに有効な支払い方法が登録されていないと、新モデルへのアクセスが拒否される。

**対処:** AWS コンソールで支払い方法を確認・更新してから再デプロイする。

## モデル更新履歴

| 日付 | モデル ID | 変更理由 |
|------|-----------|--------|
| 2025-09-30 | `global.anthropic.claude-sonnet-4-5-20250929-v1:0` | Claude Sonnet 4.5 に更新 |
| 2025-12-05 | `global.anthropic.claude-opus-4-5-20251101-v1:0` | より高性能な Opus 4.5 に更新 |
| 2026-05-27 | `global.anthropic.claude-opus-4-7` | より高性能な Opus 4.7 に更新 |
| 2026-05-30 | `global.anthropic.claude-opus-4-8` | より高性能な Opus 4.8 に更新 |
