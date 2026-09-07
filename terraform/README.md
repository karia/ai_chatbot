# デプロイ

`mise install`でツールを揃え、リポジトリルートから実行する。
環境は`ENV=dev`または`ENV=prod`で指定する。

暗号化・バージョニング・公開アクセスブロックを有効にした既存のstate用S3バケットを両環境で共有し、git管理外の`terraform/<env>.tfbackend`に接続先を書く。
S3のnative lockingを使用する。

```hcl
bucket = "<state-bucket-name>"
key    = "ai-chatbot/dev/terraform.tfstate"
```

同じバケット名を`TF_VAR_state_bucket`へ設定する。
Makefileはworkspaceの一覧取得用prefixを`ai-chatbot/<env>/workspaces`に設定する。
workspaceは`default`を使う。

Secrets Managerに`<project_name>-<environment>/slack-signing-secret`と`<project_name>-<environment>/slack-bot-token`を作り、それぞれの値を保存する。
Terraformは参照先だけを取得するため、秘密値はtfstateに取り込まない。

```sh
mise exec -- make deploy-infra ENV=dev
mise exec -- make deploy-app ENV=dev
mise exec -- terraform -chdir=terraform test
```

`deploy-infra`はLambda本体、IAM、同時実行数とイベントソースを管理し、`deploy-app`はコードと実行設定を配布する。
タイムアウトを変更した場合は両方を順に実行する。
環境別のtfvarsで`worker_timeout`を変更すると、キューの可視性タイムアウトは6倍になり、Lambdaのタイムアウトは次の`deploy-app`で更新される。

受付のプレースホルダーは503を返し、ワーカーは例外を返してメッセージを再試行に残す。
SlackのRequest URLを切り替えるのは受付とワーカーの実装後とする。
モデル選定後は`bedrock_resource_arns`に利用するモデルとInference Profileの具体的なARNを渡す。
アカウントを含むARNはgit管理外の変数ファイルに保存し、`TF_CLI_ARGS_apply=-var-file=<private-file>`で渡せる。

GitHub Actionsの初期設定、IAM権限の範囲、開発環境での動作確認と切り戻しは[デプロイ手順](../docs/runbooks/deployment.md)を参照する。
OIDC providerはdevのstateで管理し、削除保護を有効にしているため、dev全体の`destroy`は拒否する。
参照先のSecretと共有stateバケットはTerraformによる削除の対象外とする。
