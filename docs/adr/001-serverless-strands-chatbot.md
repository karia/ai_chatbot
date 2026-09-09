# ADR-001: LambdaとStrands AgentsによるSlackチャットボットの再構築

- 状態：Accepted
- 作成日：2026-09-06
- 比較対象：[ADR-002：ハイブリッド案](002-hybrid-k3s-strands-chatbot.md)
- 対象：次期チャットボットの実行基盤、非同期処理、会話保存、移行

## 決定案

既存リポジトリを継続し、Amazon API Gateway HTTP API、受付用AWS Lambda、Amazon SQS FIFO、ワーカー用AWS Lambdaで構成する。
ワーカーでPython版Strands Agentsを実行し、Amazon Bedrockを推論に、Amazon Bedrock AgentCore Memoryを会話保存に利用する。
Amazon DynamoDBには処理状態と返信の再送用データを保存する。
Amazon S3は添付・生成物の永続保存が必要になった段階で追加する。

本ADRの初期値は検証対象である。
本番切り替えは「導入手順と受け入れ条件」を満たした時点で行う。

## 背景と対象範囲

現行の[Lambdaハンドラー](../../src/lambda_function.py)は、HTTPリクエスト内で履歴取得、URL取得、推論、Slack返信を実行する。
[イベント保存](../../src/dynamodb_utils.py)は作成済みのイベントを重複として扱うため、`processing`のまま失敗したイベントの再送でも処理を終了する。
[Slack連携](../../src/slack_utils.py)は呼び出しごとにSlackからスレッド履歴を取得し、URLや添付を再取得する。
受信本文や生成結果のログ記録には、フィールド長の上限と14日の保持期間を設け、削除依頼への対応は保持期限による失効とする。[ログ方針](#セキュリティと運用)に従い、これらの条件を満たす形で記録する。

次期版は、単一Slackワークスペース内の`app_mention`を入口とし、スレッドに返信する。
会話の単位はSlackスレッドで、複数ユーザーが同じ会話に参加できる。
通常の会話、URL参照、テキスト添付、スレッド当たり50回答の上限を引き継ぐ。
DM、Slash Commands、一般メッセージの全件取り込み、書き込み系ツールは追加要件として扱う。

次期版の履歴には、ボットへのメンションとその応答を取り込む。
現行の「Slackスレッド全体を毎回読む」動作から変わり、メンションのない発言は自動で文脈に入らない。
既存スレッドの過去履歴も初期移行では取り込まず、新しいスレッドから利用を開始する。
この差分を利用案内と受け入れテストに含める。

## 追加案：Discord Gatewayだけをk3sへ配置

Discordの通常投稿とメンションを追加する場合は、Gatewayの常駐接続と受信イベントのSQS投入をk3sの専用Deploymentで行う。
SlackのHTTP受付はAPI Gatewayと受付Lambda、共通会話処理と返信はワーカーLambdaで実行する。
ECSは利用せず、k3sの担当範囲をDiscordの受信アダプターに限定する。

```mermaid
flowchart LR
    Slack[Slack Events API] --> API[API Gateway] --> Receiver[受付Lambda]
    Discord[Discord Gateway] <-->|外向きWebSocket| Gateway[k3s: Discord受信アダプター]
    Receiver --> Queue[SQS FIFO]
    Gateway --> Queue
    Gateway <--> Cursor[DynamoDB: 走査カーソルと照合状態]
    Queue --> Worker[ワーカーLambda: Strandsと返信アダプター]
    Worker --> Bedrock[Bedrock]
    Worker <--> Memory[AgentCore Memory]
    Worker <--> State[DynamoDB: 処理状態]
    Worker --> REST[SlackとDiscordの返信API]
    Worker -. ファイル保存を追加する場合 .-> S3[S3]
```

### 責務とイベント契約

| 項目 | 配置と責務 |
| --- | --- |
| Discord Gateway | k3sでHeartbeat、Resume、対象イベント選別、正規化、SQS送信 |
| 履歴照合 | k3sからDiscord REST APIを読み、DynamoDBへ走査カーソルと照合状態を保存 |
| 非同期配送 | SQS FIFOとLambdaイベントソースマッピング |
| 推論とツール | ワーカーLambdaでStrands Agentsを実行 |
| 返信 | ワーカーLambdaからDiscord REST APIへ直接投稿 |
| 会話と処理状態 | AgentCore MemoryとDynamoDB |
| 永続ファイル | 必要に応じてS3 |

SlackとDiscordのイベントは[ADR-002の共通イベント契約](002-hybrid-k3s-strands-chatbot.md#マルチアダプターの契約)へ揃える。
以下のSlack専用キュー契約を拡張し、`platform`、`installation_id`、`tenant_id`を会話キーと重複排除キーに含める。
Memoryのactorとsession、DynamoDBのキーも同じ境界で分離し、プラットフォーム間のID衝突を防ぐ。
Slackのchannel、thread timestampとDiscordのchannel、message IDは型付きの`reply_target`で区別する。

Gatewayは`SendMessage`成功後にだけ走査カーソルを進め、結果不明時は同じキーで再送する。
ワーカーLambdaがSQSを処理するため、k3s側にキューの受信ループや返信待ちキューは置かない。
Lambdaのイベントソースマッピングが受信と成功後の削除を管理する。[LambdaとSQS](https://docs.aws.amazon.com/lambda/latest/dg/with-sqs.html)
本ADRのバッチサイズ1、可視性720秒、ワーカー120秒、最大同時実行数5を使用する。
ADR-002のk3sワーカー用ポーリング、可視性180秒、同時2会話の設定は適用しない。

返信アダプターはLambda内でplatformに応じて切り替える。
Discord向けの2,000文字以内の分割、`allowed_mentions`、元投稿への参照、投稿ID保存、429への再試行は[ADR-002の返信契約](002-hybrid-k3s-strands-chatbot.md#アダプター境界)に従う。
Bot単位とルート単位のレート制限はDynamoDBで複数Lambda間に共有する。
投稿結果不明時の隔離と再処理は本ADRの状態遷移に従い、Discordの投稿IDをSlackのtsに相当する照合情報として保存する。

### Gatewayの運用と障害境界

Gatewayは1レプリカ、更新戦略Recreateとし、discord.pyのバージョンを固定する。
許可サーバーとチャンネル、通常投稿とメンションの選別、Message Content Intent、50回答上限、参加者への保存案内は[ADR-002のDiscord受信設計](002-hybrid-k3s-strands-chatbot.md#discordの通常メッセージとメンション)と[参加者への案内](002-hybrid-k3s-strands-chatbot.md#参加者への案内)に従う。
Discordからの接続は外向きであり、このPod用の公開URLやIngressは不要となる。[Discord Gateway](https://docs.discord.com/developers/events/gateway)

AWS認証には[ADR-002のIAM Roles Anywhere案](002-hybrid-k3s-strands-chatbot.md#k3sからのaws認証)を利用する。
Gatewayの権限は対象SQSへの送信とDynamoDBの走査カーソル、履歴照合状態の操作に限定する。
BedrockやMemory、キューの受信と削除はLambda実行ロールの責務とする。
Discord Bot TokenはGatewayのKubernetes Secretと返信Lambdaが読むSecrets Managerに配置し、同じBotのトークン更新を両方へ反映する。

10分以内の断絶は履歴回収と新着待機をそれぞれ1チャンネル1,000件まで行い、照合中はDynamoDBの停止状態をLambdaも確認する。
回収と新着の投入順、カーソル更新、上限超過時の扱いは[ADR-002の再接続手順](002-hybrid-k3s-strands-chatbot.md#接続と再接続)に従う。
10分を超える停止は未投入分の完全回収を保証せず、既存ジョブと返信状態を照合して利用者に再投稿を案内する。

k3s停止中もSlack受付とSQS投入済みの推論、Discordへの返信はAWS側で継続できる。
ただし、履歴照合中のまま停止したDiscord会話は、停止状態の確認と解除まで保留となる。
DLQは本ADRのイベントソース停止と復旧手順で扱う。
Gatewayの接続、最終SQS送信、AWS認証を既存PrometheusとLokiで監視し、クラスタ全停止は外部監視で検知する。

導入時には共通イベント契約、返信アダプター、AWS権限を先に整え、その後Gatewayを許可したチャンネルで有効化する。
受け入れ試験では、k3s停止中のSlack応答と投入済みDiscord返信の継続、再接続時の重複、ID衝突、トークン更新、429と投稿結果不明からの復旧を確認する。
ロールバックはGatewayの新規投入を止め、SQSに残る共通イベントを読めるLambdaを維持して残件を処理する。

## 採用判断の記録

- 決定日：2026-09-06
- 採用案：ADR-001
- 比較案：[ADR-002：ハイブリッド案](002-hybrid-k3s-strands-chatbot.md#採用判断の記録)はRejectedとする。

採用理由は次のとおりである。

- [コスト試算の仮定に基づく月額差](002-hybrid-k3s-strands-chatbot.md#両案の比較結果)は同じ利用量でSlackのみUSD 2.28、Discord込みUSD 3.52であり、運用対象を増やす理由としては小さい。
- ハイブリッド案はホスト保守、IAM Roles Anywhereの証明書運用、長期保存ファイルを扱う場合のMinIOのバックアップを増やし、数時間の停止と受信欠落の許容を前提とする。
- ADR-001ではSlackの受付と投入済みジョブの処理をホスト保守から切り離せる。

Phase 2のDiscord対応は、本ADRの追加案[Discord Gatewayだけをk3sへ配置](#追加案discord-gatewayだけをk3sへ配置)で扱う。

## 全体構成

```mermaid
flowchart TD
    Slack[Slack Events API] --> API[API Gateway HTTP API]
    API --> Ingress[受付 Lambda: 署名検証・投入]
    Ingress --> Queue[SQS FIFO: スレッド単位]
    Ingress --> ACK[HTTP 200: 3秒以内]
    Queue --> Worker[ワーカー Lambda: Strands Agents]
    Queue --> DLQ[SQS FIFO DLQ]
    Worker <--> State[DynamoDB: 処理状態・返信データ]
    Worker <--> Memory[AgentCore Memory: 会話]
    Worker --> Bedrock[Amazon Bedrock]
    Worker --> Tools[URL・添付の読み取りツール]
    Worker --> WebAPI[Slack Web API: スレッド返信]
    Secrets[Secrets Manager] --> Worker
    Ingress --> Logs[CloudWatch: メトリクス・ログ]
    Worker --> Logs
    Worker -. 将来 .-> Gateway[AgentCore Gateway: MCPツール]
    Tools -. 永続保存が必要な場合 .-> S3[S3: 添付・生成物]
```

### コンポーネントの責務

| コンポーネント | 責務 |
| --- | --- |
| API Gateway HTTP API | `POST /slack/events`のHTTPS公開 |
| 受付Lambda | Slack署名検証後のイベント投入 |
| SQS FIFO | 非同期配送とスレッド単位の直列化 |
| ワーカーLambda | 会話復元、推論、ツール実行、返信 |
| Strands Agents | モデル呼び出しとツール実行ループ |
| Amazon Bedrock | モデル推論 |
| AgentCore Memory | セッションの永続化 |
| DynamoDB | 冪等性と処理の進捗管理 |
| Secrets Manager | SlackのBot Tokenの保管 |
| CloudWatch | 障害検知と稼働状況の計測 |

Strands Agentsはアプリケーションに組み込むSDKであり、マネージドサービスとしての会話保存はAgentCore Memoryが担う。
短期記憶は会話イベントを保存し、長期記憶は会話から情報を抽出する機能を提供する。[AgentCore Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html)

エージェント本体の配置先はLambdaとし、AgentCore Runtimeは初期構成に含めない。
MemoryへはLambdaの実行ロールでAPIアクセスする。
AgentCoreの各機能は必要に応じて選択して組み合わせられる。[AgentCore FAQ](https://aws.amazon.com/bedrock/agentcore/faqs/)

## Slackの受付とURL

API GatewayはURL発行だけを目的とする場合は必須ではない。
Lambda Function URLでもHTTPSの入口を作れる。[Lambda Function URL](https://docs.aws.amazon.com/lambda/latest/dg/urls-configuration.html)

### 入口の比較

| 選択肢 | 適する条件 | 本構成での判断 |
| --- | --- | --- |
| API Gateway HTTP API | ルート管理とアクセスログを持つ小規模API | 採用 |
| Lambda Function URL | 単一関数を最小構成で公開 | 入口管理をLambdaから分離するため見送り |
| API Gateway REST API | AWS WAF連携やAPIキー単位の制御が必要 | 要件が生じた時点で再検討 |
| Slack Socket Mode | 常駐接続を維持できる実行基盤 | リクエスト駆動のLambda構成には採用しない |

HTTP APIではステージ・ルートのスロットリングとアクセスログを設定する。
AWS WAFの直接連携が必要ならREST APIを選ぶ。[HTTP APIとREST APIの比較](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-vs-rest.html)
Function URLを選ぶ場合、Slackから直接呼ぶ入口は`NONE`認証と公開用リソースポリシーが必要になり、Slack署名検証は同様に必要である。[Function URLのアクセス制御](https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html)

### 受付処理

1. HTTP APIのpayload format version 2.0を使用し、`isBase64Encoded`を反映して生のリクエスト本文を復元する。
2. JSON解析前の本文に対して`X-Slack-Signature`を検証する。`X-Slack-Request-Timestamp`と現在時刻の差を5分以内に制限し、`v0:{timestamp}:{raw_body}`のHMAC-SHA256を定数時間比較する。
3. 署名検証済みの`url_verification`には`challenge`を返す。通常イベントでは許可した`team_id`と`api_app_id`を照合し、`app_mention`かつ人間の投稿だけを対象にする。
4. 対象イベントを正規化してSQSへ`SendMessage`する。成功確認後にHTTP 200を返す。対象外イベントは200で終了する。
5. キュー投入失敗は5xx、署名不正は401、不正なペイロードは400で終了する。送信結果不明時も5xxとし、再送時の重複を後段で吸収する。

署名検証の手順は[Slack公式仕様](https://docs.slack.dev/authentication/verifying-requests-from-slack/)に従う。
Slackの受信確認期限は3秒であり、タイムアウト等でイベントが再送される。
`X-Slack-Retry-Num`だけを理由に破棄せず、同じイベントIDとして投入する。[Slack Events API](https://docs.slack.dev/apis/events-api/)

受付ではLLM、URL取得、添付取得、Slack Web APIを呼ばない。
Signing Secretは実行環境内に期限付きでキャッシュし、短いSDKタイムアウトと再試行上限を設ける。
受付成功の目標をコールドスタート込みでp99 2秒未満とし、検証で満たせなければパッケージ縮小とProvisioned Concurrencyを比較する。
Lambdaのタイムアウト設定だけではSlackの3秒制約を保証できない。

### キューの契約

`schema_version`、`event_id`、`team_id`、`api_app_id`、`channel_id`、`user_id`、`thread_ts`、`message_ts`、`text`、添付のファイルID、受信時刻を格納する。
`thread_ts`がない投稿では`message_ts`をスレッドの起点とする。
タイムスタンプは文字列のまま保持し、浮動小数点に変換しない。
本文を含む正規化済みメッセージの上限をアプリ側で128 KiBに設定し、上限超過を計測して拒否する。
添付本文と秘密情報はキューに入れない。

`MessageGroupId`は`team_id + channel_id + thread_ts`を曖昧さのない形式で連結してSHA-256にした値、`MessageDeduplicationId`は`team_id + event_id`のSHA-256とする。
同一スレッドの処理を直列化し、異なるスレッドを並列実行する。
FIFOの順序はキューへの投入順であり、Slack側で遅延したイベントまで元の発言順に並べ直す保証は持たせない。

## ワーカーと再処理

### 初期設定

| 設定 | 初期値 |
| --- | --- |
| 受付Lambdaタイムアウト | 10秒 |
| ワーカーLambdaタイムアウト | 120秒 |
| エージェント処理予算 | 90秒 |
| モデル呼び出し上限 | 8回/イベント |
| ツール呼び出し上限 | 5回/イベント |
| 出力トークン上限 | 2,048/モデル呼び出し |
| ワーカーの予約済み同時実行数 | 5 |
| SQSイベントソースの最大同時実行数 | 5 |
| SQSバッチサイズ | 1 |
| SQS可視性タイムアウト | 720秒 |
| ソースキュー保持期間 | 4日 |
| DLQ保持期間 | 14日 |
| `maxReceiveCount` | 5 |
| イベント処理状態の保持期間 | 30日 |

受付Lambdaのタイムアウトは、遅延時にもSQS投入を完了するための実行上限とする。
Slackへの応答期限は3秒のまま、API Gatewayの統合タイムアウトは5秒とし、受付Lambdaには10秒の実行時間を確保する。
10秒の実行時間は、3秒以内という応答目標を緩めるものではない。
60分間の配送試行の95%を超えて失敗するとSlackがイベント購読を一時的に無効化するため、3秒以内の応答率そのものを合格条件とする。[Slack Events API](https://docs.slack.dev/apis/events-api/)

FIFOにはバッチウィンドウを設定しない。
可視性タイムアウトはLambdaタイムアウトの6倍を初期値とする。[LambdaのSQS設定](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html)
低い同時実行数から開始し、Bedrockのトークン・リクエスト割り当てと実測値で引き上げる。

90秒の処理予算は各モデル・ツール呼び出しの前後で確認し、HTTP接続・読み取りタイムアウトも残り時間以内にする。
予算を使い切った場合は上限到達の応答を保存し、返信と後処理の時間を確保する。
Lambdaの最大実行時間は15分であるため、長時間の自律処理や承認待ちはジョブ分割、AWS Step Functions、AgentCore Runtimeを別ADRで比較する。[Lambdaタイムアウト](https://docs.aws.amazon.com/lambda/latest/dg/configuration-timeout.html)

### DynamoDBのデータ設計

新規のオンデマンドテーブルを使用し、旧会話テーブルとは分ける。

| キー種別 | 主な保存データ | 用途 |
| --- | --- | --- |
| `EVENT#<team>#<event>` | 状態、リース期限、試行番号、返信本文、Slack投稿ts、失敗分類、TTL | イベント単位の再開 |
| `SESSION#<thread hash>` | Memoryの対応先、回答数、停止理由、最終更新日時 | 会話の対応付けと停止制御 |
| `RATE#<team>#<channel>` | 次回投稿可能時刻 | チャンネル単位の投稿調整 |

イベントレコードの返信本文はUTF-8で64 KiBまでとし、上限を超える応答は短縮する。
生成物の全文保存が必要になればS3へ移し、参照先のみを保存する。
TTLは削除の予約に使用し、期限超過の判定はアプリでも行う。
セッションの停止状態は未解決の間保持し、イベントレコードが消えても自動解除しない。

### 状態遷移と障害境界

通常は`RUNNING → GENERATED → POSTING → COMPLETED`と進む。
条件付き更新で所有者と試行番号を確認し、実行時間を上回る180秒のリースを確保する。
完了済みイベントはSQS処理成功として終了する。
有効なリースを持つイベントは再試行に戻し、未完了の重複を成功扱いで捨てない。
イベントの取得とセッションの実行中イベントIDの設定はDynamoDBトランザクションで行う。
`COMPLETED`への遷移、実行中イベントIDの解除、回答数の加算も同じトランザクションにまとめ、再配送で回答数を増やさない。
別イベントが実行中のままなら後続イベントは推論を始めず、元イベントの状態を確認して再試行または隔離する。

| 障害境界 | 回復方針 |
| --- | --- |
| 推論やMemory書き込みの開始前 | リース期限後に再試行 |
| 推論中・Memory書き込み中の強制終了 | `NEEDS_REVIEW`にしてセッションを停止 |
| `GENERATED`保存後の終了 | 保存済み本文から返信を再開 |
| Slackが明示的に429を返した | `Retry-After`以降に再試行 |
| Slack投稿成功後、ts保存前の終了 | 投稿結果不明として`NEEDS_REVIEW`へ移行 |
| `COMPLETED`後のSQS再配送 | 投稿せず成功終了 |
| スキーマ不正・権限不足等の継続障害 | 再試行上限後にDLQへ移動 |

処理開始直前に実行フェーズを永続化し、次回実行が「開始前」と「実行結果不明」を区別できるようにする。
`GENERATED`はMemoryの保存完了を確認した後に記録する。
セッションマネージャーの途中保存とDynamoDB更新は単一トランザクションにならないため、途中終了を無条件に最初から実行するとユーザー発言やツール結果が重複しうる。
初期版では不明な状態を隔離し、運用者がMemoryのイベントとSlack投稿を照合して、再開または新規セッションへの切り替えを行う。
`NEEDS_REVIEW`とセッション停止をトランザクションで保存できた場合のみ、元メッセージをSQS処理成功として終了する。
保存できなければ失敗を返して配送を継続する。
停止中の後続イベントはモデルやSlackを呼ばずに失敗を返し、最終的にDLQへ残す。
将来の自動復旧ではMemoryイベントの冪等キーとチェックポイント対応を検証する。Memoryの`CreateEvent`には`clientToken`があるが、利用する連携実装が安定した値を渡すかは別に確認する。[CreateEvent](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CreateEvent.html)

`POSTING`を保存してから投稿し、成功時にSlackのtsを保存する。
通信タイムアウトや5xxでは投稿済みの可能性があるため、無条件な再投稿を止める。
既知の投稿tsがあれば`chat.update`で復旧する。
SlackとDynamoDBの間に分散トランザクションはなく、外部投稿のexactly-onceを保証しない。
複数メッセージに分割する場合は分割位置と各投稿tsを保存して同じ手順を適用する。[chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage/)

SQS FIFOの重複排除だけに依存せず、30日以内の再投入はイベントレコードで判定する。
SQSの少なくとも1回の配送を前提に実装する。[SQS可視性タイムアウト](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html)

DLQへ移動したメッセージより後の発言は進みうるため、ワーカーはセッションの停止状態を毎回確認する。
運用者はイベントソースを停止し、対象スレッドの未処理イベントを確認してから復旧する。
DLQ全件の一括再投入は通常手順にせず、後続発言を処理済みなら古い発言を会話末尾へ自動挿入しない。
必要な場合は新しいスレッドで再実行する。

### Slackへの返信制御

返信はBot Tokenを使う`chat.postMessage`で元のスレッドに送る。
Events APIのHTTP応答は受信確認として扱う。
チャンネル当たりおおむね1秒に1投稿の制限に合わせ、DynamoDBの条件付き更新で投稿枠を予約する。
複数スレッドから同じチャンネルへ返信する場合もこの制御を通す。
429の`Retry-After`が残り実行時間を超える場合は、再試行可能時刻を保存してSQSへ失敗を返す。[Slack Web APIのレート制限](https://docs.slack.dev/apis/web-api/rate-limits/)

## 会話保存とエージェント機能

### AgentCore Memoryの使い方

`bedrock-agentcore[strands-agents]`の`AgentCoreMemorySessionManager`を採用候補とし、Strandsの`Agent`へ渡す。
この連携はStrandsの資料でCommunity Contributionと位置付けられているため、SDKと連携パッケージをロックし、復元・途中終了・アップグレードの互換性を検証する。
`batch_size=1`で保存し、正常終了ではコンテキストマネージャーでクローズする。
Lambda強制終了時のflushは保証されない。[StrandsのAgentCore Memory連携](https://strandsagents.com/docs/integrations/session-managers/agentcore-memory/)

`actor_id`は環境・ワークスペース・チャンネルから導出した固定ハッシュ、`session_id`は環境・ワークスペース・チャンネル・スレッド起点から導出した固定ハッシュとする。
小文字16進のSHA-256を使い、MemoryのID文字種と長さに収める。
投稿者ごとにactorを変えると同じスレッドの履歴が分断されるため、チャンネルを会話参加主体として扱い、実際の投稿者はメッセージのメタデータに保持する。
環境ごとにMemoryリソースを分離する。

初期版は短期記憶を30日保持し、長期記憶の抽出は無効にする。
Terraformの`aws_bedrockagentcore_memory`で`event_expiry_duration`を30に設定し、長期記憶の抽出戦略のリソースは作成しない。[MemoryのTerraformリソース定義](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/bedrockagentcore_memory)
スレッドをまたぐユーザー嗜好の共有には、共有範囲と削除要件の追加設計が必要である。
後から長期記憶を有効にする場合は、まずスレッド単位の要約に限定し、namespaceをactorとsessionで分離する。
長期記憶の検索結果を認可判断や処理完了の根拠にはしない。

アプリは署名検証済みのIDからMemoryの参照先を導出し、モデルやユーザー本文に参照先を指定させない。
ワーカーは毎回新しいAgentとセッションマネージャーを作成し、同じ実行環境に別スレッドの会話オブジェクトを残さない。
履歴の保存とモデルに渡す文脈量は分けて考え、モデルの入力上限内へ履歴を制限する。
要約や履歴削減でも未完了のtool use/resultの組を壊さないことを検証する。
Strandsのセッション管理は単一の書き込み主体を前提とするため、FIFOの直列化を維持する。[Strandsのセッション管理](https://strandsagents.com/docs/user-guide/concepts/agents/session-management/)

### Storageの比較

| 選択肢 | 保存対象 | 判断 |
| --- | --- | --- |
| AgentCore Memory | 会話と将来の記憶抽出 | 採用 |
| Strands S3SessionManager | メッセージとエージェント状態 | Memory連携の検証が不成立なら再検討 |
| 独自DynamoDB会話ストア | 独自スキーマの全履歴 | 履歴復元の自作範囲が増えるため見送り |
| DynamoDB処理テーブル | 冪等性と返信再開 | 採用 |
| S3 | 大きな添付や生成物 | 永続保存要件が出た時点で追加 |
| RDS・ベクトルDB | 関係データや独自検索 | 初期要件に該当しないため保留 |

S3SessionManagerはStrands側に用意された永続化実装であり、長期記憶の抽出は別途必要になる。[S3SessionManager](https://strandsagents.com/docs/api/python/strands.session.s3_session_manager/)

### ツールと権限

初期版はURL本文取得とSlackのテキスト添付取得を、上限付きの読み取りツールとして実装する。
URLの取得はHTTPSに限定し、DNS解決後の接続先とリダイレクト先を検査してループバック、プライベート、リンクローカル等へのアクセスを拒否する。
接続先検査と実接続のずれもテストし、取得サイズを1 MiB、取得時間を10秒までに制限する。
添付は許可した種類を1イベント当たり3件まで取得し、同じサイズ上限を適用する。
Slack認証ヘッダーを外部URLやリダイレクト先に転送しない。
取得本文は信頼できない入力としてモデルに渡し、ツール権限はアプリケーションで検査する。

複数サービスのツール共有が必要になればAgentCore Gatewayを追加する。
StrandsのMCPクライアントからIAM認証で接続し、Gatewayが公開する限定したLambdaツールを呼ぶ構成とする。
API GatewayはSlackのHTTP入口、AgentCore Gatewayはエージェントのツール入口という役割で区別する。[AgentCore Gatewayの作成](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-create.html)

外部ユーザーのOAuth資格情報が必要な場合はAgentCore Identityを評価する。
BrowserやCode Interpreter、外部サービスへの書き込みは独立した権限・費用・実行時間の検証後に追加する。
書き込みには、モデルの判断とは独立した利用者認可、確認手順、ツール側の冪等キーを必須とする。

## セキュリティと運用

受付とワーカーのIAMロールを分離する。
受付には対象キューへの送信を許可する。
ワーカーには対象キューの受信・削除、処理テーブル、Memoryデータプレーン、採用するBedrockモデル、Bot Tokenへのアクセスを許可する。
Memory作成やIAM変更の権限はデプロイ用ロールに集約する。
会話内容を含むCloudWatch Logsの閲覧権限は、調査に必要な運用者のIAMロールと対象ロググループに限定する。
秘密値はSecrets Managerから取得し、環境変数には参照先を設定する。
受付が使うSigning Secretだけは例外とし、Lambdaの環境変数へ値を直接置く。

Slackは受付へのリクエストに対し3秒以内のHTTP 2xxを要求し、失敗が60分間の配送試行の95%を超えるとイベント購読を一時的に無効化する。[Slackのイベント配送要件](https://docs.slack.dev/apis/events-api/)
Signing SecretをSecrets Managerから取得すると、この3秒の予算にネットワーク呼び出しと外部サービスの可用性が加わる。
署名検証は受付の全リクエストが通る経路であり、ここを外部依存から切り離す。
値はデプロイ時に配布し、Terraformの管理下にもtfstateにも置かない。
Bot Tokenはワーカーが使い3秒の制約を受けないため、Secrets Managerのまま残す。

Slackの初期スコープは`app_mentions:read`、`chat:write`、添付取得用の`files:read`を基本とする。
Slack履歴取得を常用する機能を外すため、履歴スコープは復旧方法や将来機能で必要性を確認して追加する。
復旧時の投稿照合はSlack画面でも実施できる。

Lambdaは初期構成ではVPCに接続せず、Slackと公開HTTPSサイトへアクセスする。
プライベートデータソースが必要になった場合はVPC、エンドポイント、外向き通信経路の費用を含めて再設計する。
SQS、DynamoDB、Memory、将来のS3は暗号化を有効にし、S3には公開アクセスのブロックとライフサイクルを設定する。

ログレベルは環境変数で切り替え、環境ごとに既定値を設定する。
レベルの使い分けは次のとおりとする。

| レベル | 出力する内容 |
| --- | --- |
| ERROR | 処理の失敗と隔離 |
| WARN | 再試行、上限到達、外部サービスの制限 |
| INFO | 状態遷移、所要時間、トークン数、外部サービスへの入力と出力 |
| DEBUG | 内部処理の分岐、再試行の判定、組み立て途中の値 |

相関ID、状態、所要時間、トークン数、ツール名、エラー分類を構造化ログのフィールドとして記録し、計測値を後からメトリクスへ集計できる形にする。
INFOではBedrockへのプロンプトと応答、ツールの取得結果、Slackへの投稿本文を記録する。
1フィールド当たりの文字数に上限を設け、超過分は切り詰めたことがわかる形で落とす。
認証ヘッダー、署名、秘密値はどのレベルでも記録しない。
トレースへモデル入出力を自動収集する設定も同じ方針で確認する。
ログの初期保持期間は14日とする。
削除依頼にはMemoryの会話、将来の長期記憶、DynamoDBの返信データ、キュー内データ、ログ、将来のS3を対象として扱う。
ログへの削除依頼は、対象イベントの記録から14日の保持期間経過による失効で対応する。
CloudWatch Logsの削除操作はロググループまたはログストリーム単位であり、特定利用者のイベントだけを選択して即時削除できない。
Memory、DynamoDB、キュー内データの削除手順とは分けて、ログは保持期限による失効を依頼者へ案内する。
失効後の物理削除には通常最大72時間、まれにそれ以上かかる。[CloudWatch Logsの保持期間](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/Working-with-log-groups-and-streams.html)
Slack上の投稿削除は別の保存先として手順に含める。

### 監視と初期目標

| 指標 | 初期目標・通知条件 |
| --- | --- |
| 受付遅延 | p99 2秒未満 |
| 受付5xx | 5分間で1件以上なら通知 |
| キュー最古メッセージ | 5分超で通知 |
| DLQ件数 | 1件以上で通知 |
| `NEEDS_REVIEW`件数 | 1件以上で通知 |
| 通常応答の所要時間 | 待ち時間込みp95 60秒未満 |
| Lambdaのタイムアウト | 1件以上で通知 |
| Bedrock・Memoryのスロットリング | 件数と再試行率を計測 |

目標は外部APIの応答と負荷試験で評価し、達成できない場合はモデル、文脈量、ツール上限、同時実行数を調整する。
キュー滞留中も受付が200を返すため、受付成功率と回答完了率は別に監視する。
未解決の隔離イベントがある間は、HTTPの成功率だけで正常と判定しない。

## リージョン・モデル・費用

配置先の第一候補は東京リージョンとする。
AgentCoreは機能ごとに提供リージョンが異なるため、Memoryと追加する各機能の対応を実装開始時に確認する。[AgentCoreの対応リージョン](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html)
モデルIDまたはInference Profileは設定として外出しし、品質、tool useの互換性、レイテンシ、割り当て、料金で選ぶ。
クロスリージョン推論の利用はデータ処理先の条件と合わせて決定する。

利用量とモデル単価の仮定は[月300回答の試算](#共通の計算条件)にまとめ、実測した履歴増加と再試行を反映して更新する。

基盤費用には受付API、Lambdaのリクエスト数と実行GB秒、SQS操作、DynamoDB操作と保存量、Memoryのイベントと検索、Secrets Manager、CloudWatchを加算する。
Lambdaでは外部API待ちも実行時間として見積もる。
Memoryの長期記憶とGatewayを有効にする際は、それぞれの課金を追加する。[AgentCore料金](https://aws.amazon.com/bedrock/agentcore/pricing/)
本番前に選択モデルと東京リージョンの単価を使って見積もり、月額上限を確定する。
予算通知に加え、モデル呼び出し上限、50回答上限、同時実行上限、ワーカー停止手順を用意する。

## リポジトリとデプロイ

既存リポジトリを継続する。
Slack連携の知見、テストケース、変更履歴を活用でき、アプリケーションと構成の変更を同じPRで追跡できるためである。
新規リポジトリは、所有者・公開範囲・リリース周期を分離する必要が生じた場合に選択する。

次期実装は専用パッケージに受付、ワーカー、会話サービス、ツール、外部サービスのアダプターを分けて置く。
インフラはTerraformで管理し、関数コードの配布はlambrollが担う。
既存のlambrollによる単一関数デプロイを継続し、キュー・権限・保存先を含むインフラ定義を`terraform/`へ追加する。
IAM、DynamoDB、SQS、API Gateway、Secrets Manager、CloudWatchなどのリソースとLambda本体をTerraformで作成し、Lambdaの初回作成にはダミーzipを使用する。
lambrollの関数定義はjsonnetで記述し、環境変数はTerraformのoutputから渡す。
Makefileでは、`terraform apply`を実行する`deploy-infra`と、`lambroll deploy`を実行する`deploy-app`を分ける。
tfstateはnative S3 lockingを有効にしたS3バックエンドに保存し、バケット名はリポジトリに置かず、git管理外のbackend設定ファイルから注入する。
MemoryはTerraformのhashicorp/aws providerの`aws_bedrockagentcore_memory`で管理する。
providerのバージョン制約は`>= 6.18.0, < 7.0.0`とする。
lockファイルでは、この制約を満たすうち実際に使用する1バージョンを固定する。
短期記憶の保持日数はMemory本体で管理し、長期記憶の抽出戦略は別リソースで表す。
初期構成では抽出戦略のリソースを作成せず、長期記憶の抽出を行わない。
管理属性のdriftを検知できるが、外部から追加された未管理の抽出戦略はMemory本体のplanでは検知できない。
provider固有の問題が生じた場合はawscc providerの専用リソースを代替とする。
汎用の`aws_cloudcontrolapi_resource`はimportと通常のdrift修復に対応しないため、代替には使用しない。
採用するリージョンでリソースのライフサイクル、保持設定とdrift検知を先行検証し、追加するAgentCore機能にも同じ検証を適用する。
このADRの変更は文書に限定し、現行のデプロイ動作を維持する。

受付パッケージにはStrandsを含めず、ワーカーの依存関係をロックする。
開発環境と本番環境でTerraformの構成とtfstate、キュー、テーブル、Memory、秘密値を分ける。
`.mise.toml`でterraform、tflint、lambroll、aws-cliのバージョンを固定し、CIとローカルで揃える。
CIは単体テスト、依存関係と秘密情報の検査、IaC検証を行う。
IaC検証では`terraform/`を対象に`terraform fmt -check -recursive`、`terraform init -backend=false`、`terraform validate`、`tflint`、`trivy config .`を実行する。
本番デプロイにはGitHub ActionsのOIDCと限定したロールを使用する。
lambrollでLambdaのバージョンとエイリアスを管理し、ロールバック可能にする。

## 導入手順と受け入れ条件

1. 検証用のTerraform構成で、LambdaからBedrockとMemoryにアクセスし、Strands連携の保存・復元・権限・タイムアウトを確認する。
2. 受付、FIFO、処理状態を実装し、モデルを呼ばないワーカーで署名検証と重複・障害試験を通す。
3. 単一エージェントの会話とURL・添付ツールを実装する。Memoryの途中保存とSlack投稿結果不明の復旧を含める。
4. 別の検証用Slackアプリで負荷・障害試験を実施する。本番アプリのRequest URLはこの間変更しない。
5. 保存期間、利用量、モデル、データ処理リージョン、機能差分を確定し、運用手順をレビューする。
6. 現行処理の完了を待ち、本番アプリのRequest URLを新しい入口へ切り替える。移行時間帯のイベントを照合し、旧処理と新処理の重複実行を確認する。
7. 問題がある場合は新ワーカーのイベントソースを停止し、実行中処理を収束させてからURLを戻す。新キューの残件と投稿済みイベントを照合し、手動復旧する。
8. 安定稼働後に旧Lambda、旧テーブル、旧デプロイ経路を廃止する。旧データは保存方針に従って削除する。

### 本番切り替え前の試験

| 観点 | 合格条件 |
| --- | --- |
| Slack署名 | 改ざん、期限切れ、base64本文、ヘッダー表記差を正しく処理 |
| 受付 | コールドスタートを含め3秒以内に応答し、投入失敗を200にしない |
| Slack再送 | 投入成功後のHTTP応答消失でも最終返信が重複しない |
| 会話 | 同一スレッドの複数ユーザーで履歴を共有し、別チャンネルへ漏れない |
| FIFO | 同一スレッドへの同時投稿でMemoryの同時書き込みが発生しない |
| 状態管理 | 各永続化境界で強制終了させ、再開または隔離を確認 |
| Slack障害 | 429、5xx、投稿成功後の通信断で定義した回復方針に従う |
| Memory障害 | 部分保存とタイムアウトで無条件な会話再追加をしない |
| DLQ復旧 | 後続発言が存在するケースで会話順序を壊さず復旧できる |
| ツール | SSRF、巨大ファイル、リダイレクト、資格情報転送を拒否 |
| 制限 | 実行予算、ツール回数、出力上限、50回答上限を守る |
| 保持・削除 | 各保存先の保持設定と削除手順が機能し、ログは14日の保持期間設定と期限経過による失効を確認できる |
| ロールバック | URL復帰とキュー残件の照合を検証環境で再現できる |

### 実装開始時に確定する事項

| 項目 | 判断基準 | 確定期限 |
| --- | --- | --- |
| モデルとInference Profile | 日本語品質、ツール互換性、費用、処理先 | 推論実装前 |
| Memory連携の採用バージョン | 復元と途中失敗の互換性試験 | 会話実装前 |
| IaCのAgentCore対応 | 必要リソースと設定の再現性 | IaC実装前 |
| 月間利用量と月額上限 | 負荷想定と料金試算 | 本番切り替え前 |
| 30日の会話保持方針 | 利用者の期待と削除要件 | 本番切り替え前 |
| S3の追加 | 添付や生成物を後から再取得する必要性 | 永続ファイル機能の実装前 |
| 長期記憶・Gateway・Identity | 具体的な機能と権限共有の要件 | 各機能の追加前 |

## 帰結

Slackの受付を推論時間から分離でき、会話の永続化をマネージドサービスへ委ねられる。
一方、Memory、DynamoDB、Slack間の部分成功はアプリケーションが扱う必要がある。
初期版では不明な実行を隔離する運用負担を受け入れ、重複実行を伴う自動復旧の範囲を限定する。
将来の長時間処理、全スレッド履歴の取り込み、マルチエージェント、書き込み系ツールは、それぞれの制約を確認する追加ADRで決定する。

## コスト試算：1日2会話、各5往復、Claude Opus 5

料金確認日は2026-09-06とする。
1か月を30日とし、SlackとDiscordを合わせて1日2会話、1会話につき利用者の発言とBotの回答を5往復する。
月間では60会話、300回の回答となる。
会話ごとに新しいセッションを開始し、基本ケースでは1回答につき1回のモデル呼び出しを行う。

### 共通の計算条件

| 項目 | 仮定 |
| --- | --- |
| モデル | Claude Opus 5 |
| 推論方式 | StandardのGlobal Cross-Region |
| 基盤のリージョン | ap-northeast-1 |
| システムプロンプト | 1,000トークン/呼び出し |
| 利用者の新規入力 | 500トークン/往復 |
| モデルの出力 | thinkingを含む課金対象の合計1,000トークン/往復 |
| 会話履歴 | 同じ会話の過去の入出力を毎回再入力 |
| キャッシュ割引 | 計上しない |
| ツール実行と再試行 | 基本ケースには含めない |
| 添付と生成ファイル | 基本ケースには含めない |
| 税と為替 | USDの税抜額を基準とする |
| 無料枠とクレジット | 毎月適用される無料枠も含め、控除前の保守的な金額で比較する |

Opus 5の標準料金は入力100万トークン当たりUSD 5、出力100万トークン当たりUSD 25を使用する。[AnthropicのOpus 5提供案内](https://www.anthropic.com/news/claude-opus-5)
東京の`bedrock-runtime`からは`global.anthropic.claude-opus-5`を使う条件で算定する。
この呼び出しは東京内に処理を限定しないため、本番採用時にデータ処理先を確認する。[BedrockのOpus 5モデル仕様](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5.html)

出力の1,000トークンは表示される回答だけの長さではない。
thinking等の課金対象もこの枠に含め、過去の出力を全量再入力する保守的な計算にする。
実際に再入力されないthinkingやキャッシュヒットは費用を下げ、長い添付、追加のモデル呼び出し、再試行は費用を上げる。

### 推論費用の計算

n往復目の入力は`1,000 + 500 × n + 1,000 × (n − 1)`トークンとなる。

| 往復目 | 入力トークン数 | 出力トークン数 |
| --- | ---: | ---: |
| 1 | 1,500 | 1,000 |
| 2 | 3,000 | 1,000 |
| 3 | 4,500 | 1,000 |
| 4 | 6,000 | 1,000 |
| 5 | 7,500 | 1,000 |
| 1会話の合計 | 22,500 | 5,000 |

月間入力は`22,500 × 60 = 1,350,000`、月間出力は`5,000 × 60 = 300,000`トークンとなる。
推論費用は`1.35 × USD 5 + 0.30 × USD 25 = USD 14.25/月`、1会話当たりUSD 0.2375である。

### 回答量による推論費用の差

各ケースで過去の出力を全量再入力し、システムプロンプトと利用者入力は固定する。

| 1往復の出力トークン数 | 月間推論費用 USD |
| ---: | ---: |
| 500 | 9.00 |
| 1,000 | 14.25 |
| 2,048 | 25.25 |

AgentCore Memoryはユーザー発言と回答を別イベントとして保存する仮定で、`60 × 5 × 2 = 600`イベントとなる。
短期記憶のUSD 0.25/1,000イベントから、`600 ÷ 1,000 × 0.25 = USD 0.15/月`を計上する。
SDKが追加イベントを作成する場合は実測値で更新する。
長期記憶は無効とする。[AgentCore料金](https://aws.amazon.com/bedrock/agentcore/pricing/)

DynamoDBはトランザクション、項目サイズ、リース更新を含め、1回答当たり100 WRUと10 RRUの計算枠を置く。
月間30,000 WRU、3,000 RRU、保存0.01 GBとして、`30,000 × 0.000000715 + 3,000 × 0.0000001425 + 0.01 × 0.285 = USD 0.02473/月`となる。
この枠は固定のAPI呼び出し回数を意味せず、実装後にConsumedCapacityで置き換える。[DynamoDBの東京料金データ](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonDynamoDB/current/ap-northeast-1/index.json)

### AWS中心案の月額

受付Lambdaは256 MiBで平均0.2秒、ワーカーは1 GiBで平均30秒、各300回を仮定する。
予約済み同時実行数の設定には常時起動の費用を加えず、Provisioned ConcurrencyとイベントソースのProvisioned Modeは無効とする。

| 費目 | 月間計算 | 月額 USD |
| --- | --- | ---: |
| Opus 5 | 共通の推論計算 | 14.25000 |
| AgentCore Memory | 600イベント | 0.15000 |
| DynamoDB | 30,000 WRU、3,000 RRU、0.01 GB | 0.02473 |
| Lambda | 9,015 GB秒と600リクエスト | 0.15037 |
| HTTP API | 300 × 0.00000129 | 0.00039 |
| SQS FIFO | 648,900 × 0.0000005 | 0.32445 |
| Secrets Manager | 1秘密値 × 0.40 + 300取得 × 0.000005 | 0.40150 |
| CloudWatch | 下記のメトリクス、アラーム、ログ | 2.30777 |
| 外向き転送 | 0.01 GB × 0.114 | 0.00114 |
| S3 | ファイル保存を追加するまで0 | 0.00000 |
| 合計 | 丸め前の金額を合算 | 17.61034 |

LambdaにはUSD 0.0000166667/GB秒とUSD 0.20/100万リクエスト、HTTP APIにはUSD 1.29/100万リクエストを使用した。[Lambdaの東京料金データ](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSLambda/current/ap-northeast-1/index.json)、[API Gatewayの東京料金データ](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonApiGateway/current/ap-northeast-1/index.json)

SQSには送信、受信、削除の900回に、待機中の空ポーリングを加える。
20秒のlong pollingが5本動く仮定では、`5 × 30 × 86,400 ÷ 20 = 648,000`回となる。
これは見積もり用の仮定であり、Lambda管理下のポーリング数や課金額の上限を保証する値ではない。
ペイロードは64 KiB以下とし、FIFOのUSD 0.50/100万リクエストを使う。[SQSの東京料金データ](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSQueueService/current/ap-northeast-1/index.json)

CloudWatchは集約したカスタムメトリクス5個、標準アラーム8個、ログ取り込み0.01 GB、平均ログ保存0.005 GBを仮定する。
`5 × 0.30 + 8 × 0.10 + 0.01 × 0.76 + 0.005 × 0.033 = USD 2.307765/月`となる。
イベントIDをメトリクスのdimensionに使わず、メトリクス数の増加を防ぐ。[CloudWatchの東京料金データ](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonCloudWatch/current/ap-northeast-1/index.json)

秘密値はワーカーが使うSlack Bot Tokenの1件とする。受付のSigning Secretは環境変数へ移したため計上しない。[Secrets Managerの東京料金データ](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSSecretsManager/current/ap-northeast-1/index.json)
外向き転送には無料枠控除前の東京単価を使う。[転送の東京料金データ](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSDataTransfer/current/ap-northeast-1/index.json)
S3を追加し、平均1 GB、月100 PUTと100 GETを使う場合は、`0.025 + 100 × 0.0000047 + 100 × 0.00000037 = USD 0.025507/月`を加算する。[S3の東京料金データ](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/ap-northeast-1/index.json)

### Discord Gatewayだけをk3sで常駐させる場合

既存のk3sホストと回線を使用し、Gatewayのログとメトリクスは既存のPrometheusとLokiへ保存する。
AWS側には返信Lambdaが取得するDiscord Bot Tokenを1秘密値、月100回の取得として追加する。
IAM Roles Anywhereは自己管理CAを使い、サービスの追加料金を0とする。
DynamoDBのカーソル更新は基本表の計算枠内に含め、導入後に実測する。

| 追加費目 | 月間計算 | 月額 USD |
| --- | --- | ---: |
| Discord Bot Token | 0.40 + 100 × 0.000005 | 0.40050 |
| IAM Roles Anywhere | 自己管理CA | 0.00000 |
| AWS側の追加合計 | 上記の合算 | 0.40050 |

Secrets Managerには[東京料金データ](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSSecretsManager/current/ap-northeast-1/index.json)を使用する。
CAを別途契約する場合は固定費を追加する。[IAM Roles Anywhereの料金案内](https://aws.amazon.com/about-aws/whats-new/2022/07/aws-identity-access-management-iam-roles-anywhere-workloads-outside-aws/)

月間300回の回答を両プラットフォームで分け合い、モデルとSQSの費用は基本表の枠内で計上する。
受付費用は全300件がSlack経由という基本表を据え置く保守的な比較とし、Discord受信分の受付LambdaとHTTP APIの減少は控除しない。
SQSの受信はLambdaが管理するため、Gateway用の空ポーリング費用は追加しない。
AWS側は`USD 17.6103398005 + 0.40050 = 約USD 18.01/月`となる。

Gatewayと監視の増分電力に5 W、電力単価に35円/kWhの仮定を置くと、`5 ÷ 1,000 × 720 × 35 = 126円/月`となる。
1 USD＝150円の比較用換算では、`18.0108398005 + 126 ÷ 150 = 約USD 18.85/月`となる。
ADR-002も同じ5 Wの計算枠を使用しており、Gatewayだけの場合の省電力効果は実測まで織り込まない。
既存ホストと回線の新設費用、証明書運用、設備増設は別途加算する。

金額には開発と運用の人件費、既存Slack／Discord契約、CIとイメージ保管、独自ドメイン、任意の追加バックアップや顧客管理KMSキーを含めない。
無料枠が残っていればLambda、SQS、CloudWatch等の実請求は下がる。
一方、Provisioned Concurrency、長い推論、追加ツール、添付の取り込み、再試行を有効にした場合は再計算する。
