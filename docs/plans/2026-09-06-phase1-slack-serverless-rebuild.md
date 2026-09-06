# Phase 1: Slackチャットボットのサーバーレス再構築

- 対象ADR：[ADR-001：LambdaとStrands AgentsによるSlackチャットボットの再構築](../adr/001-serverless-strands-chatbot.md)
- 範囲：Slackの`app_mention`を入口とする新経路の構築、本番切り替え、旧実装の廃止
- Phase 2：Discord対応。Phase 1完了後に別計画として作成する

Phase 1はADR-001の「導入手順と受け入れ条件」を14個のPull Requestに割り当てたものである。
既存のLambda実装は新経路が本番へ切り替わるまで動かし続け、最後のPull Requestで廃止する。

## 全体方針

すべてのPull Requestで次の3点を守る。

### テスト駆動開発

t-wadaメソッドのテスト駆動開発で実装する。
失敗するテストを書き、最小の実装で通し、重複を除く順に進める。
各Pull Requestの検証の節に挙げた条件は、テストとして書けるものから先に書く。

### Twelve-Factor App

Twelve-Factor Appの方法論に従う。
設定は環境変数から読み、秘密値は参照先だけを環境変数に置く。
依存はビルドルートごとに宣言し、バージョンを固定する。
ログはイベントストリームとして標準出力へ書き、保存と検索は実行環境に任せる。
開発環境と本番環境の差は設定だけに閉じる。

### ログレベルと計測

ログレベルを環境変数で切り替えられるようにし、環境ごとに既定値を設定する。
レベルの使い分けを次のとおりとする。

| レベル | 出力する内容 |
| --- | --- |
| ERROR | 処理の失敗と隔離 |
| WARN | 再試行、上限到達、外部サービスの制限 |
| INFO | 状態遷移、所要時間、トークン数、外部サービスへの入力と出力 |
| DEBUG | 内部処理の分岐、再試行の判定、組み立て途中の値 |

計測に使う値は構造化ログのフィールドとして出し、後からメトリクスへ集計できる形にする。

INFOでは、Bedrockへのプロンプトと応答、ツールの取得結果、Slackへの投稿本文を、何を送って何が返ったかが追える形で記録する。
1フィールド当たりの文字数に上限を設け、超過分は切り詰めたことがわかる形で落とす。
認証ヘッダー、署名、秘密値は、どのレベルでも出さない。

ADR-001は本文をログに残さない方針を採るため、この差分はPR 1でADR-001側にも反映する。
保持期間は14日とし、削除依頼の対象にログを含める。

## 進め方

作業は1Pull Requestごとに完結させ、単体で検証できる成果物を残す。
複数の担当が並行して着手できるよう、依存関係とWaveを下表に示す。
同じWaveのPull Requestは互いに参照せずに進められる。

先行するPull Requestの成果物に触れる必要があるときは、そのPull Requestがmergeされたあとのmainからbranchを作成する。
1つのPull Requestが複数のWaveにまたがる作業を含む場合は、そのPull Requestを分割する。

### Pull Requestの一覧と依存関係

| Wave | PR | 主題 | 依存 |
| --- | --- | --- | --- |
| 1 | 1 | ADR-001の採用記録 | なし |
| 1 | 2 | CIの整備 | なし |
| 1 | 3 | Strands AgentsとAgentCore Memoryの疎通検証 | なし |
| 2 | 4 | 本番構成のIaC | 3 |
| 3 | 5 | 受付Lambda | 4 |
| 3 | 6 | DynamoDBの状態管理 | 4 |
| 3 | 9 | 読み取りツール | 2 |
| 3 | 12 | デプロイパイプライン | 4 |
| 4 | 7 | Slack返信アダプター | 6 |
| 5 | 8 | ワーカーのオーケストレーション | 5, 6, 7 |
| 6 | 10 | 会話サービス | 3, 8, 9 |
| 7 | 11 | 監視とログ | 10 |
| 8 | 13 | 受け入れ試験と切り替え手順 | 10, 11, 12 |
| 9 | 14 | 本番切り替えと旧実装の廃止 | 13 |

### ファイル配置

新実装は受付とワーカーを別のビルドルートに置き、受付側にStrands Agentsを持ち込まない。

| パス | 責務 |
| --- | --- |
| `src/ingress/` | Slack署名検証、イベント選別、SQS投入 |
| `src/worker/` | 状態管理、エージェント実行、ツール、Slack返信 |
| `src/worker/requirements.txt` | ワーカーだけが使う依存の固定 |
| `tests/unit/ingress/` | 受付の単体テスト |
| `tests/unit/worker/` | ワーカーの単体テスト |
| `terraform/` | インフラのリソースと環境別の変数定義 |
| `src/ingress/function.jsonnet` | lambrollによる受付Lambdaの関数定義 |
| `src/worker/function.jsonnet` | lambrollによるワーカーLambdaの関数定義 |
| `Makefile` | `deploy-infra`と`deploy-app`によるインフラと関数コードのデプロイ |
| `.mise.toml` | CIとローカルで使うツールバージョンの固定 |
| `docs/runbooks/` | 切り替えと復旧の手順書 |

キューのメッセージ契約は受付とワーカーの双方が保持し、送信時と受信時の両方で検証する。
共有モジュールをLambda Layerへ切り出すのは、重複が契約定義だけに収まらなくなった時点でよい。

既存の`src/*.py`とdeploy.shは、PR14まで手を入れずに残す。

### ADRで実装開始時に確定するとした事項

| 事項 | 確定するPull Request |
| --- | --- |
| Memory連携の採用バージョン | 3 |
| IaCのAgentCore対応 | 3 |
| モデルとInference Profile | 10 |
| 月間利用量と月額上限 | 13 |
| 30日の会話保持方針 | 13 |
| S3の追加 | Phase 1では扱わない |
| 長期記憶、Gateway、Identity | Phase 1では扱わない |

## PR 1: ADR-001の採用記録

**目的**：ADR-001を採用として確定し、後続の実装が参照できる状態にする。

- 変更：`docs/adr/001-serverless-strands-chatbot.md`、`docs/adr/002-hybrid-k3s-strands-chatbot.md`
- 「採用判断の記録」に決定日、採用案、理由を記録する
- ADR-001の状態をAcceptedに、ADR-002の状態を選定結果への参照付きで更新する
- ADR-001のログ方針を、本計画の「ログレベルと計測」に合わせて更新する

**検証**：両ADRの状態と相互参照のリンクが一致することを目視で確認する。

## PR 2: CIの整備

**目的**：以降のPull Requestが自動検証を受けられる状態にする。

- 作成：`.github/workflows/ci.yml`、`.mise.toml`
- 変更：`requirements-dev.txt`、`pytest.ini`
- 単体テスト、依存関係の脆弱性検査、秘密情報の検査を実行する
- `.mise.toml`でterraform、tflint、lambroll、aws-cliのバージョンを固定し、CIとローカルで同じバージョンを使う
- IaC検証のジョブは、`terraform/`の追加後に有効化できる形で用意する
- IaC検証は`terraform/`で`terraform fmt -check -recursive`、`terraform init -backend=false`、`terraform validate`、`tflint`、`trivy config .`を実行する
- `src/ingress/`と`src/worker/`の両方をテスト対象に含められるよう、import解決の設定を先に入れる

**検証**：`./run_tests.sh`が通り、Pull Request上でCIが成功する。

**詰まりやすい点**：既存テストは`--cov=.`で全体を対象にしているため、ビルドルートを分けた後のカバレッジ対象を明示する必要がある。

## PR 3: Strands AgentsとAgentCore Memoryの疎通検証

**目的**：ADR-001が前提とするMemory連携が成立することを確認し、依存バージョンを固定する。

- 作成：`terraform/`の初版、`docs/verification/agentcore-memory.md`
- hashicorp/aws providerのバージョン制約を`>= 6.18.0, < 7.0.0`とする
- lockファイルでは、この制約を満たすうち実際に使用する1バージョンを固定する
- `aws_bedrockagentcore_memory`と検証用Lambda1本の構成を東京リージョンへ作成する
- `event_expiry_duration`を30日に設定し、抽出戦略のリソースを作成せずに、保存、復元、権限、タイムアウトを確認する
- セッションの途中終了時に何が保存され何が失われるかを記録する
- 確認できたSDKと連携パッケージのバージョンを固定する

**検証**：Terraformのapplyと再applyが再現し、同一`session_id`で会話が復元できる。
Memoryの削除、Memory IDによるimport、管理属性のdrift検知と修復を確認する。

**詰まりやすい点**：未管理の抽出戦略はMemory本体のplanでは検知できないため、抽出戦略が追加されていないことを別途確認する。
provider固有の問題が出た場合はawscc providerの専用リソースを検証する。
連携パッケージはCommunity Contributionの位置付けであり、想定どおり動かない場合はStrandsのS3SessionManagerへ切り替える判断をこのPull Requestで下す。

**保存方式を切り替える場合**：PR 4へ進む前に、ADR-001と本計画を改訂する。
改訂では保存先、権限、保持方針を決め直し、会話復元と途中失敗の試験を後続のPull Requestへ割り当て直す。
S3を使う場合はPhase 1の対象に加え、暗号化、公開アクセスのブロック、ライフサイクルの設定を含める。

## PR 4: 本番構成のIaC

**目的**：新経路のリソースをTerraformの構成として定義し、開発環境と本番環境を分ける。

- 変更：`terraform/`
- 作成：`terraform/`内の環境別の変数定義
- API Gateway HTTP API、SQS FIFO、FIFOのDLQ、DynamoDBテーブル、Secrets Managerの参照を定義する
- 受付とワーカーのIAMロールを分離し、それぞれに必要な権限だけを与える
- ADR-001の「初期設定」のうち、環境差と運用上の調整が必要な値をパラメーターとして外に出し、残りはTerraformの定義内に持つ
- Lambda本体はTerraformで作成し、初回はダミーzipを配置する
- lambrollで配布するコードと実行設定をTerraformの再applyで上書きしないよう、管理範囲を分ける
- tfstateはnative S3 lockingを有効にしたS3バックエンドへ保存し、バケット名はgit管理外のbackend設定ファイルから注入する

**検証**：`terraform/`で`terraform fmt -check -recursive`、`terraform init -backend=false`、`terraform validate`、`tflint`、`trivy config .`が通り、開発環境へのデプロイと削除が再現する。

**詰まりやすい点**：可視性タイムアウトとワーカーのタイムアウトの関係、`maxReceiveCount`、予約済み同時実行数は、ADR-001の初期値をそのまま入れる。

## PR 5: 受付Lambda

**目的**：Slackの3秒制約を満たす受付処理を実装する。

- 作成：`src/ingress/app.py`、`src/ingress/signature.py`、`src/ingress/events.py`、`tests/unit/ingress/`
- 変更：`terraform/`
- payload format version 2.0の本文復元、署名検証、`url_verification`応答を実装する
- `team_id`と`api_app_id`を照合し、人間による`app_mention`だけを対象にする
- 正規化したメッセージをSQSへ送り、送信成功を確認してから200を返す
- `MessageGroupId`と`MessageDeduplicationId`をADR-001の定義どおりに導出する
- 本文サイズの上限を超えるメッセージを拒否し、件数を計測する

**検証**：改ざん、期限切れ、base64本文、ヘッダー表記差の各ケースで単体テストが通る。
開発環境でSlackアプリのRequest URLを向け、コールドスタートを含む応答時間を測る。

**詰まりやすい点**：署名検証はJSON解析前の生の本文に対して行う。
投入失敗を200で返さないことをテストで固定する。

## PR 6: DynamoDBの状態管理

**目的**：イベントの再開とセッションの停止制御を担うデータアクセス層を作る。

- 作成：`src/worker/store.py`、`tests/unit/worker/test_store.py`
- イベント、セッション、投稿枠の3種類のキーを扱う操作を実装する
- 条件付き更新で所有者と試行番号を確認し、180秒のリースを取得する
- イベントの取得とセッションの実行中イベントIDの設定を1つのトランザクションにまとめる
- `COMPLETED`への遷移、実行中イベントIDの解除、回答数の加算も同じトランザクションにまとめる
- TTLの設定と、期限超過のアプリ側判定を実装する

**検証**：motoを使い、同時実行と再配送を模したテストで回答数が二重に増えないことを確認する。

**詰まりやすい点**：セッションの停止状態はイベントレコードのTTL失効で解除されない扱いにする。

## PR 7: Slack返信アダプター

**目的**：投稿の重複と429を制御した返信経路を作る。

- 作成：`src/worker/slack.py`、`tests/unit/worker/test_slack.py`
- `chat.postMessage`でスレッドへ返信し、投稿後にtsを保存する
- 投稿枠の予約とtsの保存はPR 6のデータアクセス層を呼び、DynamoDBの操作をここで作り直さない
- 429の`Retry-After`が残り実行時間を超える場合は再試行可能時刻を保存して失敗を返す
- 既知のtsがある場合は`chat.update`で復旧する
- 分割投稿では分割位置と各tsを保存する

**検証**：429、5xx、投稿成功後の通信断をスタブで再現し、無条件な再投稿が起きないことを確認する。

**詰まりやすい点**：投稿枠の予約はスレッドをまたいで同じチャンネルへ返信する場合にも通す。
PR 6に足りない操作があれば、このPull Requestでデータアクセス層に追加し、呼び出し側には置かない。

## PR 8: ワーカーのオーケストレーション

**目的**：推論を伴わない状態でワーカーの冪等性と障害境界を完成させる。

- 作成：`src/worker/app.py`、`src/worker/pipeline.py`、`tests/unit/worker/test_pipeline.py`
- 変更：`terraform/`
- `RUNNING`から`COMPLETED`までの遷移と、各永続化境界での再開または隔離を実装する
- 応答生成の代わりに固定文言を返し、受付から返信までを通す
- 停止中のセッションでは外部呼び出しを行わずに失敗を返す
- `NEEDS_REVIEW`への移行とセッション停止をトランザクションで保存する
- 処理予算の残り時間を各段階の前後で確認する

**検証**：各境界で強制終了させ、再開または隔離のどちらになるかを確認する。
同一スレッドへの同時投稿で直列化されることを開発環境で確認する。

**詰まりやすい点**：完了済みイベントの再配送は投稿せずに成功終了とし、有効なリースを持つイベントは再試行へ戻す。

## PR 9: 読み取りツール

**目的**：URLとテキスト添付の取得を上限付きの読み取りツールとして実装する。

- 作成：`src/worker/tools/url.py`、`src/worker/tools/attachments.py`、`tests/unit/worker/test_tools.py`
- 取得先をHTTPSに限定し、DNS解決後の接続先とリダイレクト先を検査する
- ループバック、プライベート、リンクローカルへのアクセスを拒否する
- 取得サイズを1 MiB、取得時間を10秒に制限する
- 添付は許可した種類を1イベント当たり3件まで取得する
- Slackの認証ヘッダーを外部URLとリダイレクト先へ転送しない
- 取得した本文は信頼できない入力として扱う

**検証**：SSRF、巨大ファイル、リダイレクト、資格情報転送を拒否するテストが通る。
接続先の検査と実際の接続がずれるケースもテストする。

**詰まりやすい点**：エージェントへの登録はPR 10で行うため、このPull Requestではツール単体の入出力と上限だけを固める。

## PR 10: 会話サービス

**目的**：Strands AgentsとAgentCore Memoryによる会話を組み込む。

- 作成：`src/worker/conversation.py`、`tests/unit/worker/test_conversation.py`
- 変更：`src/worker/pipeline.py`、`src/worker/requirements.txt`
- `actor_id`と`session_id`を署名検証済みのIDから導出する
- イベントごとに新しいAgentとセッションマネージャーを作り、実行環境に会話を残さない
- モデルIDまたはInference Profileを設定として外に出す
- PR 9のツールをエージェントへ登録する
- モデル呼び出し回数、ツール呼び出し回数、出力トークン、スレッド当たり50回答の上限を実装する
- Memoryの保存完了を確認してから`GENERATED`を記録する
- モデルへ渡す文脈量を入力上限内へ制限し、未完了のtool useとtool resultの組を壊さない

**検証**：同一スレッドの複数ユーザーで履歴が共有され、別チャンネルへ漏れないことを確認する。
Memoryの部分保存とタイムアウトで、会話が無条件に再追加されないことを確認する。

**詰まりやすい点**：履歴の保存量とモデルへ渡す量は別に扱う。
モデル選定は日本語品質、tool useの互換性、費用、データ処理先で判断し、選定結果を設定の既定値として残す。

## PR 11: 監視とログ

**目的**：ADR-001の初期目標を計測し、隔離イベントを検知できるようにする。

- 変更：`terraform/`、`src/ingress/`、`src/worker/`
- 作成：`docs/runbooks/alerts.md`
- 受付遅延、受付5xx、キュー最古メッセージ、DLQ件数、`NEEDS_REVIEW`件数、タイムアウトのアラームを定義する
- BedrockとMemoryのスロットリングを計測する
- 相関ID、状態、所要時間、トークン数、ツール名、エラー分類が、受付とワーカーの全経路で揃っているかを確認し、欠けているフィールドを足す
- ログの保持期間を14日に設定する
- トレースへモデル入出力が自動収集されない設定になっているかを確認する

**検証**：意図的にDLQへ送り、通知が届くことを確認する。
認証ヘッダー、署名、秘密値がログとトレースに含まれないこと、長い入出力が上限で切り詰められることをテストで固定する。

**詰まりやすい点**：構造化ログの出力そのものは各実装のPull Requestで「全体方針」に従って行う。
このPull Requestでは、揃った出力を対象にアラームと保持設定を組む。

## PR 12: デプロイパイプライン

**目的**：本番デプロイを自動化し、ロールバック手段を用意する。

- 作成：`.github/workflows/deploy.yml`、`Makefile`、`src/ingress/function.jsonnet`、`src/worker/function.jsonnet`
- 変更：`terraform/`、`.github/workflows/ci.yml`
- GitHub ActionsのOIDCと限定したロールでデプロイする
- Makefileの`deploy-infra`で`terraform apply`、`deploy-app`で`lambroll deploy`を実行し、インフラと関数コードのデプロイを分ける
- lambrollの関数定義はjsonnetで記述し、環境変数はTerraformのoutputから渡す
- 開発環境と本番環境でTerraformの構成とtfstate、キュー、テーブル、Memory、秘密値を分ける
- lambrollでLambdaのバージョンとエイリアスを管理し、直前バージョンへ戻せるようにする
- PR 2で用意したCIのIaC検証ジョブを有効化する

**検証**：開発環境への自動デプロイと、エイリアスの切り戻しを実行して確認する。

## PR 13: 受け入れ試験と切り替え手順

**目的**：本番切り替えの合否を判定し、手順を文書として残す。

- 作成：`docs/runbooks/slack-cutover.md`、`docs/verification/phase1-acceptance.md`
- 検証用のSlackアプリで負荷試験と障害試験を実施し、ADR-001の試験表の合格条件を1件ずつ判定する
- 本番アプリのRequest URLはこの間変更しない
- 月間利用量と月額上限、30日の会話保持方針を確定する
- 切り替え手順、ロールバック手順、DLQ復旧手順を書く
- メンションのない発言が文脈に入らない変更点を利用案内に含める

**検証**：試験表の全項目に判定結果と根拠を記載する。
ロールバックを検証環境で1度通す。

**詰まりやすい点**：DLQの一括再投入は通常手順にしない。
後続の発言を処理済みの場合は、新しいスレッドでの再実行を案内する。

## PR 14: 本番切り替えと旧実装の廃止

**目的**：本番トラフィックを新経路へ移し、旧経路を削除する。

- 削除：`src/*.py`の旧モジュール、`deploy.sh`、対応する旧テスト
- 変更：`README.md`
- 現行処理の完了を待ってから、本番アプリのRequest URLを新しい入口へ切り替える
- 移行時間帯のイベントを照合し、旧処理と新処理の重複実行を確認する
- 安定稼働を確認した後に旧Lambda、旧テーブル、旧デプロイ経路を廃止する
- 旧データは保存方針に従って削除する

**検証**：切り替え後の24時間で、回答完了率とアラート発生状況を確認する。

**詰まりやすい点**：問題があればワーカーのイベントソースを停止し、実行中の処理を収束させてからURLを戻す。
旧実装の削除は切り替えの成功確認後に行う。確認に日数を置く場合は、切り替えと削除を別のPull Requestに分ける。
