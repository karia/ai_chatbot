# Phase 2: Discord対応

- 要件：[PRD: Discord対応](../prd/2026-09-24-discord-support.md)

## Pull Requestの一覧

| PR | リポジトリ | 内容 | 前提 |
| --- | --- | --- | --- |
| 1 | ai_chatbot | ワーカーのDiscord対応と、返信用Bot Tokenの参照 | なし |
| 2 | ai_chatbot | Discord受信アダプターと、コンテナイメージのビルド | PR 1 |
| 3 | ai_chatbot | k3sからAWSへの認証と、受信アダプター用IAMロール | なし |
| 4 | yuno04-k3s | 受信アダプターのマニフェスト | PR 2、PR 3 |

PR 1とPR 3は並行して進められる。
PR 3は、IAM Roles Anywhereで短期資格情報を取得できることを最初に確かめる。取得できなければOIDCに切り替える。

## PR 1：ワーカーのDiscord対応

### 変更するファイル

| ファイル | 変更 |
| --- | --- |
| `src/worker/pipeline.py` | キューメッセージの検証と返信先の選択を、プラットフォームで分岐させる |
| `src/worker/store.py` | 処理状態、セッション、レート制限のキーにプラットフォームの区別を加える |
| `src/worker/conversation.py` | Memoryのactorとsessionの導出、プロンプト内のプラットフォーム名 |
| `src/worker/discord_reply.py` | 新規。Discord REST APIへの返信、スレッドの作成、分割、429の扱い |
| `src/worker/requirements.in`、`requirements.txt` | Discordへの返信に依存を足す場合のみ |
| `terraform/main.tf`、`iam.tf`、`outputs.tf`、`deployment.tf` | Discord Bot Tokenのシークレット参照と、ワーカーの読み取り権限 |
| `tests/unit/worker/` | 上記に対応するテスト |
| `docs/runbooks/deployment.md` | Discord Bot Tokenのシークレット登録手順 |

### 作業順序

1. Slackのキューメッセージが従来どおり検証を通り、同じキーとMemoryのセッションに解決されることをテストで固定する。
2. Discordのキューメッセージの形を決め、検証を追加する。
3. キーとMemoryのセッションの導出にプラットフォームを加える。Slackの値は変えない。
4. Discordへの返信アダプターを追加し、プラットフォームで返信先を切り替える。
5. Terraformにシークレット参照と権限を追加する。

### 検証

- `./run_tests.sh`と、CIの`terraform test`が通る。
- devへのデプロイ後、Slackでの会話が従来どおり動く。

## PR 2：Discord受信アダプター

### 変更するファイル

| ファイル | 変更 |
| --- | --- |
| `src/discord_gateway/` | 新規。Gatewayへの接続、許可範囲とメンションの選別、PR 1で決めた形でのキュー投入 |
| `src/discord_gateway/Dockerfile` | 新規。amd64のイメージ |
| `.github/workflows/` | イメージをビルドしてghcrへpushするワークフロー |
| `tests/unit/discord_gateway/` | 選別とキューメッセージへの変換のテスト |
| `run_tests.sh`、`requirements-dev.txt` | 新しいテストを既存のテスト実行に含める |

### 検証

- 選別とキューメッセージへの変換をテストで確認する。
- mainへのマージ後、ghcrにcommit SHAのタグ付きイメージができる。

## PR 3：k3sからAWSへの認証

### 変更するファイル

| ファイル | 変更 |
| --- | --- |
| `terraform/` | トラストアンカー、プロファイル、キューへの送信だけを許すIAMロール |
| `docs/runbooks/` | 証明書の発行、k3sへの配置、更新の手順 |

### 検証

- 発行した証明書で取得した資格情報で、対象キューへ送信でき、それ以外の操作が拒否される。

## PR 4：受信アダプターのマニフェスト

`karia/yuno04-k3s`の`apps/`配下に、1レプリカ・更新戦略`Recreate`のDeployment、許可するサーバーとチャンネルのConfigMap、READMEを置く。
Discord Bot Tokenと証明書は、READMEの手順で`kubectl create secret`により投入する。

### 検証

PRDの完了条件をDiscordサーバーで確認する。

## 詰まりそうな箇所

| 箇所 | 注意点 |
| --- | --- |
| 既存のSlackデータ | キューに残るSlackのメッセージ、DynamoDBのキー、Memoryのsessionの導出は、デプロイ前後で同じ値にする |
| スレッドと会話の単位 | 投稿から作ったスレッドのIDは元の投稿のIDと一致するため、チャンネルでのメンションとそのスレッド内のメンションが同じ会話になる |
| メンションの本文 | 本文にBotへのメンション記法が含まれるため、プロンプトに渡す前に取り除く |
| Discordのレート制限 | 429の`retry_after`と、Bot全体に掛かるグローバル制限を区別する |
| スレッド作成の再試行 | 投稿結果が不明なまま再試行すると、スレッドの作成が既に済んでいることがある |
| 受信アダプターのイベントループ | キューへの送信でGatewayのHeartbeatを止めない |
| デプロイロールの権限 | IAM Roles Anywhereのリソース作成はデプロイロールの権限と権限境界に関わるため、CIでは失敗する。管理者の認証情報で適用する |
| #52との競合 | 画像添付の対応が`src/worker/conversation.py`と`src/worker/tools/attachments.py`を変更中のため、先にマージされた側に追従する |
