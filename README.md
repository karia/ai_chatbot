# AI Chatbot

このプロジェクトは、AWSのサービスを利用したAIチャットボットアプリケーションです。SlackインターフェースとAmazon Bedrockを使用して、動的な対話を実現します。

このREADMEを含め、ほとんどのソースコードはAI生成です。

## アーキテクチャ

このチャットボットは以下のAWSサービスを使用しています：
- AWS Lambda: Slackイベントの処理とメッセージの処理を担当
- Amazon Bedrock: レスポンス生成のためのClaude 3 Sonnet AIモデルを提供
- Amazon DynamoDB: 会話履歴とイベントデータを保存
- API Gateway: SlackイベントのHTTPエンドポイントを提供

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
  - API Gateway
- アプリを追加する権限を持つSlackワークスペース
- デプロイメント用の[lambroll](https://github.com/fujiwara/lambroll)

## Deployment

1. このリポジトリをローカルマシンにクローンします。

2. デプロイスクリプトを実行します：
   ```bash
   ./deploy.sh
   ```

3. Lambdaに環境変数を設定します。必要な環境変数は：
   - `SLACK_BOT_TOKEN`: SlackボットトークンS
   - `DYNAMODB_TABLE_NAME`: DynamoDBテーブル名

4. LambdaのトリガーとしてAPI Gatewayを設定します。

5. Slackイベントサブスクリプションを設定します：
   - リクエストURLをAPI GatewayエンドポイントにポイントS
   - `app_mention`イベントをサブスクライブ

6. Slackでボットを招待し、メンションを送信することで対話を開始できます。ボットに適切な権限を付与してください。

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

## License

[MIT](https://choosealicense.com/licenses/mit/)
