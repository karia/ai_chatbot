# AI Chatbot

このプロジェクトは、AWSのサービスを利用したAIチャットボットアプリケーションです。SlackインターフェースとAmazon Bedrockを使用して、動的な対話を実現します。

このREADMEを含め、ほとんどのソースコードはAI生成です。

## アーキテクチャ

このチャットボットは以下のAWSサービスを使用しています：
- AWS Lambda: Slackイベントの処理とメッセージの処理を担当
- Amazon Bedrock: レスポンス生成のためのClaude 3 Sonnet AIモデルを提供
- Amazon DynamoDB: 会話履歴とイベントデータを保存

## 機能

- Slackチャンネルとスレッドでのメンションに応答
- メッセージ内で共有されたURLの処理と理解
- テキストファイルの添付ファイルの処理
- スレッド内での会話コンテキストの維持
- 共有URLからのコンテンツの抽出と処理
- DynamoDBへの会話履歴の保存
- 過剰な使用を防ぐための単一スレッドでのレスポンス数の制限

## 前提条件

- 以下へのアクセス権を持つAWSアカウント：
  - Lambda
  - DynamoDB
  - Bedrock（Claude 3 Sonnetモデルが有効）
- アプリを追加する権限を持つSlackワークスペース
- デプロイメント用の[lambroll](https://github.com/fujiwara/lambroll)

## Deployment

1. [Slack api -> Your Apps](https://api.slack.com/apps) で Create New App してください。

2. OAuth & Permissions -> Scopes -> Bot Token Scopes で以下権限を付与してください。
   - app_mentions:read
   - channels:history
   - chat:write
   - files:read
   - groups:history
   - im:history
   - mpim:history

3. OAuth & Permissions -> Install to Workspace でボットをワークスペースにインストールしてください。

4. このリポジトリをローカルマシンにクローンしてください。

5. デプロイスクリプトを実行します：
   ```bash
   ./deploy.sh
   ```

6. Lambdaの設定で、適切なIAMおよび環境変数を設定します。

7. Lambdaの設定で関数URLを発行してください

8. Slack Api -> Event Subscriptions で以下を設定したあと、Save Changes してください。
   - Enable Events をONにする
   - Request URL にLambdaのエンドポイントを入れる
   - Subscribe to bot events で以下を追加
     - app_mention

9. Slackでボットを招待し、メンションを送信することで対話を開始できます。

## 設定

チャットボットは環境変数と`config.py`ファイルを通じて設定されます：

- 環境変数（Lambdaで設定する必要があります）：
  - `SLACK_BOT_TOKEN`: Slackボットトークン
  - `DYNAMODB_TABLE_NAME`: DynamoDBテーブル名

- `config.py`での設定：
  - `AI_MODEL_MAX_TOKENS`: AIレスポンスの最大トークン数（デフォルト：2048）
  - `AI_MODEL_ID`: BedrockモデルID（デフォルト：Claude 3 Sonnet）
  - `AI_MODEL_VERSION`: Bedrock APIバージョン

## 使用方法

1. ボットをSlackチャンネルに招待します。

2. チャンネルまたはスレッドでボットにメンションして会話を開始します：
   ```
   @ボット名 こんにちは、何か手伝ってもらえますか？
   ```

3. ボットはスレッド内で応答します。

4. URLを共有したりテキストファイルを添付したりすると、ボットはそのコンテンツを処理します。

5. 会話コンテキストはスレッド内で維持され、複数ターンの会話が可能です。

## テスト

このプロジェクトには、ユニットテストが含まれています。テストは各モジュールの機能を検証し、リファクタリングやコード変更時のバグ混入を防ぎます。

### テスト環境のセットアップ

1. テスト用の依存関係をインストールします：
   ```bash
   pip install -r requirements-dev.txt
   ```

### テストの実行方法

テスト実行用のスクリプトを使用して、すべてのテストを実行できます：
```bash
./run_tests.sh
```

特定のテストファイルのみを実行する場合：
```bash
./run_tests.sh tests/unit/test_utils.py
```

カバレッジレポートを生成する場合：
```bash
./run_tests.sh --cov-report=html
```

### conftest.pyについて

`tests/conftest.py`ファイルは、pytest用の共通設定とフィクスチャを提供します：

1. **環境変数の設定**：
   - テスト実行時に必要な環境変数（`SLACK_BOT_TOKEN`や`DYNAMODB_TABLE_NAME`など）を自動的に設定します。
   - 実際のAPIキーやトークンを使用せずにテストを実行できます。

2. **共通フィクスチャ**：
   - `slack_event_fixture`：Slackイベントのモックデータを提供
   - `mock_bedrock_response`：AWS Bedrock APIレスポンスのモック
   - `aws_credentials`：AWS認証情報のモック

3. **使い方**：
   - テストファイル内でフィクスチャを引数として使用するだけで、自動的に適用されます：
     ```python
     def test_function(slack_event_fixture):
         # slack_event_fixtureを使用したテスト
         ...
     ```
   - 環境変数は`autouse=True`で設定されているため、明示的に呼び出す必要はありません。

4. **カスタマイズ**：
   - 新しいモックやフィクスチャが必要な場合は、`conftest.py`に追加することで、すべてのテストで利用可能になります。

## License

[MIT](https://choosealicense.com/licenses/mit/)
