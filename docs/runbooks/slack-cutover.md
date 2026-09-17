# Slack本番切り替え

本番SlackアプリのEvents APIを新しい受付へ切り替える。
切り替え前に旧Request URLを記録し、問題があれば新ワーカーを止めてから元へ戻す。

## 前提と確認事項

次の条件をすべて満たしてから切り替える。

- ActionsのDeployを`prod`に対して実行し、インフラとアプリケーションのデプロイが成功している。
- `ai-chatbot-prod-`で始まるCloudWatchアラームがすべて`OK`である。
- 本番のDLQに可視メッセージと処理中メッセージがない。
- Slackアプリに`app_mentions:read`、`chat:write`、`files:read`のBot Token Scopeがあり、scopeを変更した場合はワークスペースへ再インストールしている。
- SlackアプリのEvent Subscriptionsが有効で、Bot Eventsに`app_mention`が登録されている。
- 現在のRequest URLを、ロールバック用の`<previous-request-url>`として安全な作業記録へ控えている。

アラームとDLQは次のコマンドで確認する。
値をリポジトリへ保存しない。

```sh
mise exec -- aws cloudwatch describe-alarms \
  --alarm-name-prefix ai-chatbot-prod- \
  --query 'MetricAlarms[].[AlarmName,StateValue]' \
  --output table

DLQ_URL="$(TF_DATA_DIR="$PWD/terraform/.terraform-prod" mise exec -- terraform -chdir=terraform output -raw dlq_url)"
mise exec -- aws sqs get-queue-attributes \
  --queue-url "$DLQ_URL" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
```

両方のメッセージ数が`0`であることを確認する。
アラームの調査方法は[アラーム対応](alerts.md)に従う。

## 切り替え手順

1. Slackで処理中のメンションがないことを確認する。
   処理中のメンションがあれば、返信が完了するか、運用者が隔離を判断するまで待つ。
2. 新しい受付URLを取得する。

   ```sh
   SLACK_EVENTS_URL="$(TF_DATA_DIR="$PWD/terraform/.terraform-prod" mise exec -- terraform -chdir=terraform output -raw slack_events_url)"
   printf '%s\n' "$SLACK_EVENTS_URL"
   ```

3. Slackアプリ管理画面のEvent Subscriptionsを開き、Request URLを`$SLACK_EVENTS_URL`の出力値へ変更する。
   Slackの検証が成功したことを確認して保存する。
4. 新しいSlackスレッドでBotへ1回メンションする。
   同じスレッドに返信が1回だけ投稿されることを確認する。
5. CloudWatchアラームがすべて`OK`のままで、DLQのメッセージ数が`0`であることを再確認する。

## ロールバック手順

1. 新ワーカーのイベントソースマッピングを特定し、停止する。

   ```sh
   mise exec -- aws lambda list-event-source-mappings \
     --function-name ai-chatbot-prod-worker:current \
     --query 'EventSourceMappings[].[UUID,State]' \
     --output table
   EVENT_SOURCE_UUID="$(mise exec -- aws lambda list-event-source-mappings \
     --function-name ai-chatbot-prod-worker:current \
     --query 'EventSourceMappings[0].UUID' \
     --output text)"
   mise exec -- aws lambda update-event-source-mapping \
     --uuid "$EVENT_SOURCE_UUID" \
     --no-enabled
   mise exec -- aws lambda get-event-source-mapping \
     --uuid "$EVENT_SOURCE_UUID" \
     --query '[State,LastProcessingResult]' \
     --output table
   ```

   対象が1件であることと、状態が`Disabled`になったことを確認する。
2. 実行中の新ワーカーが完了するまで待つ。
   投稿結果を確定できないイベントは`NEEDS_REVIEW`として隔離し、再実行しない。
3. Slackアプリ管理画面でRequest URLを記録済みの`<previous-request-url>`へ戻し、Slackの検証が成功したことを確認して保存する。
4. 新キューの待機中メッセージと処理中メッセージを確認する。

   ```sh
   QUEUE_URL="$(mise exec -- aws lambda get-function-configuration \
     --function-name ai-chatbot-prod-ingress \
     --qualifier current \
     --query 'Environment.Variables.QUEUE_URL' \
     --output text)"
   mise exec -- aws sqs get-queue-attributes \
     --queue-url "$QUEUE_URL" \
     --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
   ```

5. キューに残る各イベントを`event_id`で構造化ログ、DynamoDBの処理状態、Slackスレッドと照合する。
   Slackへ投稿済みのイベントは再実行しない。
   投稿されていないイベントだけを未処理として記録し、旧経路で手動対応するか、後日の再切り替え時に処理する。

イベントソースマッピングは、残件と`NEEDS_REVIEW`によるセッション停止を解消し、新経路へ再度切り替えるときまで停止したままにする。

## DLQ復旧手順

DLQの確認、個別再投入、消去は[アラーム対応のDLQの処理](alerts.md#dlqの処理)に従う。
切り替え中は先に新ワーカーのイベントソースマッピングを停止し、実行中の処理を完了または隔離する。

同じSlackスレッドの後続メンションが処理済みであれば、古いイベントをその後へ再投入しない。
処理順を回復できないイベントは、新しいスレッドで改めてメンションして実行する。
残件とセッション停止理由を解消した後に限り、イベントソースマッピングを再開する。

```sh
mise exec -- aws lambda update-event-source-mapping \
  --uuid "$EVENT_SOURCE_UUID" \
  --enabled
mise exec -- aws lambda get-event-source-mapping \
  --uuid "$EVENT_SOURCE_UUID" \
  --query '[State,LastProcessingResult]' \
  --output table
```

状態が`Enabled`になったことを確認する。

## 利用案内

- Botへのメンションだけを受け付ける。
- 同じスレッド内でも、Botへのメンションがない発言は会話の文脈へ入らない。
- 1つのスレッドでBotが回答できる回数は100回である。上限に達したら新しいスレッドを開始する。

## 30日の会話保持方針

会話の保持期間は次のとおり決定している。

| 保存先 | 保持期間 | 設定箇所 |
| --- | --- | --- |
| AgentCore Memoryの短期記憶 | 30日 | `terraform/main.tf`の`event_expiry_duration` |
| DynamoDBのイベント記録 | 30日 | `src/worker/store.py`の`EVENT_TTL_SECONDS`と`terraform/storage.tf`のTTL |
| 受付Lambda、ワーカーLambda、APIアクセスログ | 14日 | `terraform/lambda.tf`と`terraform/api.tf`の`retention_in_days` |

AgentCore Memoryの長期記憶抽出は有効にしていない。
