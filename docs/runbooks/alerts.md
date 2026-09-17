# アラーム対応

CloudWatchアラームは環境ごとのSNSトピックへ通知する。
通知を受けたら対象環境、アラーム名、発生時刻を確認し、同じ時刻の構造化ログを`correlation_id`で追跡する。

## 用語

| 用語 | 意味 |
| --- | --- |
| イベント | Slackのメンション1件に対する処理の単位。`correlation_id`はSlackのevent IDを指す |
| セッション | 1つのSlackスレッドに対応する会話の単位。同時に処理するイベントは1件に限る |
| `NEEDS_REVIEW` | イベントの終了状態の1つ。自動では再開せず、人が状態を確認して判断する。返信の有無が確定できない場合や、再実行すると二重投稿になり得る場合に使う |
| セッション停止 | `NEEDS_REVIEW`に伴いセッションを止めた状態。解除するまで、同じスレッドの後続イベントは処理しない |
| 隔離 | 再試行しても成功しないイベントを、キューから取り除いて終了させること |

状態遷移の定義は[ADR-001の状態遷移と障害境界](../adr/001-serverless-strands-chatbot.md#状態遷移と障害境界)に従う。

## アラーム一覧

### 通知条件と初動

| アラーム末尾 | 通知条件 | 最初に確認する内容 |
| --- | --- | --- |
| `ingress-latency` | 受付の応答時間が、直近5分間のp99で2秒以上 | API Gatewayの5xx、受付LambdaのDuration、SQS送信エラーを確認する |
| `ingress-5xx` | 受付の5xxが直近5分間に1件以上 | APIアクセスログのrequest IDから受付Lambdaの`state`と`error_class`を確認する |
| `queue-age` | 最古メッセージの待ち時間が5分を超過 | ワーカーLambdaのエラー、スロットリング、同時実行数を確認する |
| `dlq-messages` | DLQの可視メッセージが1件以上 | メッセージを隔離した原因を調査し、再処理または破棄を決める |
| `needs-review` | `NEEDS_REVIEW`への遷移が1件以上 | `correlation_id`に対応する失敗分類を確認し、セッション停止を解除する前に投稿状態を照合する |
| `lambda-timeouts` | 残り時間の不足による打ち切りが1件以上 | `stage`と残り時間を確認し、外部APIの遅延とワーカーのタイムアウト設定を比較する。Lambda本体のタイムアウトは`worker-errors`で検知する |
| `answer-response-time` | 受信から投稿完了までの所要時間が、直近5分間のp95で60秒以上 | `answer_completed`の`response_ms`とステージ別の`duration_ms`を比較し、待ち時間と生成時間のどちらが伸びたかを切り分ける |
| `session-stopped` | 停止中のセッションへイベントが1件以上到着 | 先行する`NEEDS_REVIEW`を解決し、保留イベントを再処理する順序を決める |
| `ingress-errors` | 受付LambdaのErrorsが直近5分間に1件以上 | 受付Lambdaの最新エラーとAPI Gatewayの応答を確認する |
| `worker-errors` | ワーカーLambdaのErrorsが直近5分間に1件以上 | SQSの再試行回数、ワーカーの`state`、`error_class`を確認する |

メトリクスがない期間は正常として扱う。
`ai-chatbot-<env>/Monitoring`の`ServiceThrottles`は`Service`ディメンションで`bedrock`と`memory`を分ける。
Bedrockの`InvocationThrottles`とAgentCore Memoryの`Throttles`もCloudWatchの標準メトリクスで確認する。

## SNS通知の購読

対象環境のTerraform出力からトピックARNを取得し、購読を作成する。
通知先はリポジトリへ保存しない。

```sh
TOPIC_ARN="$(TF_DATA_DIR="$PWD/terraform/.terraform-<env>" mise exec -- terraform -chdir=terraform output -raw alarm_topic_arn)"
mise exec -- aws sns subscribe \
  --topic-arn "$TOPIC_ARN" \
  --protocol email \
  --notification-endpoint '<email-address>'
```

通知先へ届く確認メールのリンクを開き、購読状態が`Confirmed`になったことを確認する。

```sh
mise exec -- aws sns list-subscriptions-by-topic --topic-arn "$TOPIC_ARN"
```

## DLQの処理

DLQを消去する前に、可視メッセージ数と本文を確認する。
本文には会話内容が含まれるため、端末の履歴や共有ログへコピーしない。

```sh
DLQ_URL="$(TF_DATA_DIR="$PWD/terraform/.terraform-<env>" mise exec -- terraform -chdir=terraform output -raw dlq_url)"
mise exec -- aws sqs get-queue-attributes \
  --queue-url "$DLQ_URL" \
  --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible
mise exec -- aws sqs receive-message \
  --queue-url "$DLQ_URL" \
  --max-number-of-messages 1 \
  --visibility-timeout 30
```

原因を修正し、必要なイベントを元のキューへ再投入するか、Slackで手動対応する。
全メッセージの対応結果を確認したあと、DLQを消去する。
`purge-queue`はDLQ内のメッセージを復元できないため、対象URLを再確認してから実行する。

```sh
mise exec -- aws sqs purge-queue --queue-url "$DLQ_URL"
```

消去の反映には最大60秒かかる。
属性を再取得し、可視メッセージ数が0になったことを確認する。

## ログとトレース

受付Lambda、ワーカーLambda、APIアクセスログの保持期間は14日である。
アプリケーションログは相関ID、状態、所要時間、トークン数、ツール名、エラー分類を構造化フィールドとして出力し、文字列を1,000文字以内へ切り詰める。
認証ヘッダー、Slack署名、秘密値はログ境界で除去する。

Lambdaのトレースモードは`PassThrough`である。
このモードは受信したX-Rayヘッダーを下流へ渡すが、Lambdaからトレースを自動送信しない。
AgentCore Memoryの拡張スパンとログ、Amazon Bedrockのモデル呼び出しログ、OpenTelemetryの自動計装も有効化していないため、モデル入力と出力はトレースへ自動収集されない。
