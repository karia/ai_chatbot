# AI Chatbot

このプロジェクトは、AWSのサービスを利用したAIチャットボットアプリケーションです。SlackインターフェースとAmazon Bedrockを使用して、動的な対話を実現します。

このREADMEを含め、ほとんどのソースコードはAI生成です。

## Deployment

1. 依存パッケージを含むZIPファイルを作成します：

```bash
./make_packages.sh
```

2. 作成されたZIPファイルをAWS Lambdaにアップロードします。

3. Lambdaに環境変数を設定します。必要な環境変数は `config.py` を参照してください。

4. LambdaのトリガーとしてAPI GatewayおよびSlackのイベントサブスクリプションを設定します。

5. Slackでボットを招待し、メンションを送信することで対話を開始できます。ボットに適切な権限を付与してください。

## License

[MIT](https://choosealicense.com/licenses/mit/)
