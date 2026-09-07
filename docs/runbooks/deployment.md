# デプロイと切り戻し

mainへのpushでdevへ自動デプロイする。
手動実行はActionsのDeployから`main`と`dev`または`prod`を選ぶ。
prodはGitHub Environmentの承認後に適用する。
Terraformがインフラを管理し、lambrollがLambdaの公開バージョンと`current`エイリアスを更新する。

## 初回設定

### AWSとTerraform

管理者のAWS認証情報を使い、[Terraformの初期設定](../../terraform/README.md)に従って既存のS3バックエンドとSecrets Managerを準備する。
環境ごとのstate keyは`ai-chatbot/<env>/terraform.tfstate`とし、Terraform workspaceは`default`を使う。
バケット名はgit管理外の`terraform/<env>.tfbackend`と環境変数`TF_VAR_state_bucket`へ設定する。
両方に同じ名前を指定する。

```sh
mise install
export TF_VAR_state_bucket='<existing-state-bucket>'
mise exec -- make deploy-infra ENV=dev
mise exec -- make deploy-infra ENV=prod
```

devのstateがアカウント共通のGitHub OIDC providerを所有し、各環境のstateが自身のデプロイロールとアプリの権限境界を所有する。
初回適用はdev、prodの順に行う。
OIDC providerには削除保護があり、dev全体の`terraform destroy`も拒否する。

各環境のロールARNを取得し、次節のGitHub変数へ設定する。

```sh
TF_DATA_DIR="$PWD/terraform/.terraform-dev" mise exec -- terraform -chdir=terraform output -raw deploy_role_arn
TF_DATA_DIR="$PWD/terraform/.terraform-prod" mise exec -- terraform -chdir=terraform output -raw deploy_role_arn
```

デプロイロール自身、OIDC provider、権限境界の変更は管理者の認証情報で適用する。
API GatewayのIDとイベントソースのタグ操作対象をIAMポリシーに固定しているため、これらのリソースの置き換えも管理者が行う。
アプリ用IAMロールの権限境界を外す権限もCIには与えていないため、ロールの置き換え・削除は管理者が行う。
Bedrockのモデル許可を変更するときは、同じ`TF_VAR_bedrock_resource_arns`を管理者の適用とGitHub変数の両方へ渡す。

### GitHub

リポジトリに`dev`と`prod`のEnvironmentを作成する。
両方のDeployment branches and tagsを`main`だけに限定する。
prodにはRequired reviewersを設定し、Prevent self-reviewを有効にする。
管理者による保護ルールの迂回も無効にする。
Environmentの承認設定はworkflowファイルでは作成されないため、自動デプロイを有効にする前に設定する。

必要なActions変数は次のとおり。

| 変数 | 設定先 | 値の意味 |
| --- | --- | --- |
| `TF_STATE_BUCKET` | リポジトリ | 既存のstate保存用S3バケット名。両環境で共通 |
| `AWS_DEPLOY_ROLE_ARN` | 各Environment | その環境のTerraform出力`deploy_role_arn` |
| `BEDROCK_RESOURCE_ARNS` | 各Environment | 許可するモデル・Inference Profileの具体的なARNを並べたJSON配列。省略時は`[]` |

追加のGitHub Secretsは不要である。
AWSの長期アクセスキーは登録しない。
Slackの秘密値はSecrets Managerに保存し、Lambdaへは参照先だけを渡す。

管理者が次のコマンドでリポジトリのOIDC subjectを設定する。
信頼ポリシーとclaimの順序を一致させるため、キーの順番を保持する。

```sh
gh api --method PUT repos/karia/ai_chatbot/actions/oidc/customization/sub --input - <<'JSON'
{
  "use_default": false,
  "include_claim_keys": ["repo", "context", "ref", "event_name"]
}
JSON
```

Environmentを指定した標準subjectにはブランチ情報が含まれない。
このカスタマイズで、リポジトリ、環境、`refs/heads/main`、`push`または`workflow_dispatch`をIAMの`StringEquals`で照合する。
設定はリポジトリ内で発行されるOIDCトークン全体に適用される。
詳しくは[GitHubのOIDC subjectカスタマイズ](https://docs.github.com/en/actions/reference/security/oidc#customizing-the-subject-claims-for-an-organization-or-repository)を参照する。

## デプロイ権限

ロールごとの操作範囲は次のとおり。

| 対象 | 許可する操作と範囲 |
| --- | --- |
| S3 | 自環境のstateとlockの読み書き、lock削除。バケット一覧取得は該当keyと自環境のworkspace prefixに限定 |
| Lambda | 自環境の受付・ワーカーの構成、コード、バージョン、エイリアス、呼び出し許可、同時実行数の管理 |
| イベントソース | `lambda:FunctionArn`で自環境のワーカーに限定。タグ操作は既存マッピングのARNに限定 |
| IAM | 自環境のアプリ用2ロールとインラインポリシーの管理。ロール作成には固定の権限境界が必須。PassRoleはLambdaだけ |
| CloudWatch Logs | 自環境のロググループの作成・保持設定・削除。ログ配送とリソースポリシーの管理はリージョン内 |
| SQSとDynamoDB | 自環境のキューと状態テーブルの構成管理 |
| AgentCore Memory | 作成時はリージョンと環境タグで限定。既存Memoryの管理は環境別の名前prefixで限定 |
| Secrets Manager | 自環境のSlack用Secretのメタデータ参照 |
| API Gateway | 現在のAPI ID配下の管理。新規APIの作成は自環境のAPI名に限定 |

Terraformによる更新に必要な管理操作を明示している。
AdministratorAccessやサービス全体の`Action: "*"`は与えていない。
イベントソースとMemoryの作成にはリソースARNを指定できないため、`Resource: "*"`と条件を組み合わせる。
HTTP APIのアクセスログ設定には、ログ配送の管理と`PutResourcePolicy`を含むリージョン共通の権限も必要になる。
これは他プロジェクトのログ配送・リソースポリシーにも及ぶ広い権限であり、環境単位の分離が残る制約となる。
ログ内容の読み取り権限は付与しない。
必要な操作は[AWSのHTTP APIログ設定](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-logging.html#http-api-logging-permissions)に従う。
アプリの権限境界は、自環境のログ、Slack用Secret、キュー、状態テーブル、Memoryと明示したBedrockモデルだけに実行権限の上限を設ける。
デプロイロールはSecretの値を直接取得できないが、Lambdaのコードとアプリ用ロールを変更できるため、アプリがアクセスするデータへの間接的なアクセス権限を持つ。

## 開発環境での動作確認

1. mainへのpush後、ActionsのDeployがdevを選び、インフラ適用とアプリ配布まで成功することを確認する。
2. 実行のSummaryにある受付とワーカーのデプロイ前バージョンを記録する。
3. 次のコマンドで両方の`current`が数値の公開バージョンを指していることを確認する。
4. 開発用Slackアプリからメンションを送り、受付からワーカーまで処理されることを確認する。
5. もう一度デプロイし、次節の切り戻しを実行する。切り戻し後も開発用Slackで確認する。

```sh
mise exec -- aws lambda get-alias --function-name ai-chatbot-dev-ingress --name current --query FunctionVersion --output text
mise exec -- aws lambda get-alias --function-name ai-chatbot-dev-worker --name current --query FunctionVersion --output text
```

初回の新規作成時はTerraformがダミーコードを公開してエイリアスを作る。
実アプリの配布が一度成功したあと、次のデプロイで切り戻しを検証する。
API GatewayとSQSイベントソースは`current`を呼び、Terraformは作成済みエイリアスのバージョンを上書きしない。
受付・ワーカーの実装と秘密値の設定が揃ってからSlackによる確認を行う。

prodについては手動実行し、承認待ちの間にAWS認証と適用が開始されないことを確認する。
main以外のブランチで手動実行した場合はジョブがskipされることを確認する。

## 直前の稼働バージョンへの切り戻し

対象環境のデプロイが終了していることを確認し、切り戻し中は新しいデプロイを開始しない。
Actions内の同時デプロイは環境ごとに直列化されるが、ローカル操作はこの制御に含まれない。
prodでは運用担当者の承認を得てから、対象環境の認証情報で実行する。

失敗したデプロイのSummaryから、更新前に`current`が指していた番号を取得する。
現在の番号も`get-alias`で確認し、更新された関数だけを戻す。
次の変数には、それぞれ記録した番号を設定する。

```sh
INGRESS_VERSION='<recorded-ingress-version>'
WORKER_VERSION='<recorded-worker-version>'
mise exec -- make rollback-app ENV=dev FUNCTION=worker VERSION="$WORKER_VERSION"
mise exec -- make rollback-app ENV=dev FUNCTION=ingress VERSION="$INGRESS_VERSION"
```

`rollback-app`は指定した関数の`current`だけをlambrollで更新する。
Terraformの適用とビルドは実行せず、公開済みバージョンも削除しない。
`get-alias`を再実行して記録した番号との一致を確認し、開発用Slackで動作を確認する。

受付とワーカーの更新は別々のAPI呼び出しであり、2関数を同時には切り替えられない。
片方だけ更新された場合は、成功した側の番号を確認してから必要な関数を戻す。
バージョン番号の単純な減算は、途中失敗や過去の切り戻しで直前の稼働版と一致しないため使わない。
[lambrollのrollback](https://github.com/fujiwara/lambroll#rollback)には記録した番号を`--version`で明示する。
実行中のLambda、キューのメッセージ、インフラ設定、投稿済みのSlack応答はエイリアス変更では巻き戻らない。
