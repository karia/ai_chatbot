# デプロイ

`mise install`でツールを揃え、リポジトリルートから実行する。
環境は`ENV=dev`または`ENV=prod`で指定する。

環境ごとに、暗号化・バージョニング・公開アクセスブロックを有効にしたstate用S3バケットを用意し、git管理外の`terraform/<env>.tfbackend`に接続先を書く。
S3のnative lockingを使用する。

```hcl
bucket = "<state-bucket-name>"
key    = "ai-chatbot/dev/terraform.tfstate"
```

Secrets Managerに`<project_name>-<environment>/slack-signing-secret`と`<project_name>-<environment>/slack-bot-token`を作り、それぞれの値を保存する。
Terraformは参照先だけを取得するため、秘密値はtfstateに取り込まない。

```sh
mise exec -- make deploy-infra ENV=dev
mise exec -- make deploy-app ENV=dev
mise exec -- terraform -chdir=terraform test
mise exec -- make destroy ENV=dev
```

`deploy-infra`はLambda本体、IAM、同時実行数とイベントソースを管理し、`deploy-app`はコードと実行設定を配布する。
タイムアウトを変更した場合は両方を順に実行する。
環境別のtfvarsで`worker_timeout`を変更すると、キューの可視性タイムアウトは6倍になり、Lambdaのタイムアウトは次の`deploy-app`で更新される。

受付のプレースホルダーは503を返し、ワーカーは例外を返してメッセージを再試行に残す。
SlackのRequest URLを切り替えるのは受付とワーカーの実装後とする。
モデル選定後は`bedrock_resource_arns`に利用するモデルとInference Profileの具体的なARNを渡す。
アカウントを含むARNはgit管理外の変数ファイルに保存し、`TF_CLI_ARGS_apply=-var-file=<private-file>`で渡せる。

`destroy`は参照先のSecretとstate用バケットを削除しない。
検証用に作成した場合は、検証後にSecretとバケット内の全バージョンを削除してからバケットを削除する。
