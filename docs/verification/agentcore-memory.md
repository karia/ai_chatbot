# AgentCore Memoryの調査と検証

## 調査結果

2026年9月6日時点の公式資料とPyPI配布物では、東京リージョンで短期記憶を30日保持し、長期記憶を抽出しない構成を定義できる。
インフラ管理にはTerraform、Lambdaのコード配布にはlambrollを使用する。
TerraformのAWS providerにMemoryの専用リソースがあり、この構成で実機検証へ進める条件は揃っている。
Memoryを管理するprovider経路はAWS providerの専用リソースとし、実機で保存と復元、権限、タイムアウト、途中終了、Terraformによる作成と更新を確認した。
検証用のリソースは確認後に削除した。

## 東京リージョンでの提供

AWSの提供リージョン表では、`AgentCore Memory`行と`Asia Pacific (Tokyo)`列の交点が`✓ Yes`になっている。
次の表は該当箇所の抜粋である。[Supported AWS Regions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html)

| Feature | Asia Pacific (Tokyo) |
| --- | --- |
| AgentCore Memory | ✓ Yes |

東京のリージョンコードは`ap-northeast-1`である。
AWSのエンドポイント表にも東京のエンドポイントが掲載されている。[Amazon Bedrock AgentCore endpoints and quotas](https://docs.aws.amazon.com/general/latest/gr/bedrock_agentcore.html)

## CloudFormationとAWS SAMでの比較

この節はTerraform経路の比較材料として、CloudFormationでの定義を示す。

Memoryのリソースタイプは`AWS::BedrockAgentCore::Memory`である。
必須プロパティは`Name`と`EventExpiryDuration`の2つであり、保持期間と抽出設定は次のプロパティに対応する。[CloudFormationのMemory定義](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-memory.html)

| プロパティ | 必須 | 用途 |
| --- | --- | --- |
| `Name` | はい | Memoryの名前 |
| `EventExpiryDuration` | はい | 短期記憶のイベント保持日数 |
| `MemoryStrategies` | いいえ | 長期記憶の抽出戦略の配列 |
| `MemoryExecutionRoleArn` | いいえ | Memoryが使用するIAMロール |

`Name`は`^[a-zA-Z][a-zA-Z0-9_]{0,47}$`に一致する必要がある。
`EventExpiryDuration`は3〜365の整数で指定する。
長期記憶の抽出を行わない構成では`MemoryStrategies`を省略する。

AWS SAMの`Resources`にはCloudFormationリソースを混在させられるため、Memoryも同じテンプレートで管理できる。[AWS SAM template anatomy](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-specification-template-anatomy.html)
対象設定の定義例を以下に示す。

```yaml
Resources:
  ConversationMemory:
    Type: AWS::BedrockAgentCore::Memory
    Properties:
      Name: VerifyAgentCoreMemory
      EventExpiryDuration: 30
```

保持期間と抽出戦略の更新はCloudFormation上で`No interruption`、名前の変更は`Replacement`と定義されている。
Lambdaへ渡すMemory IDは`!GetAtt ConversationMemory.MemoryId`で取得する。
`!Ref ConversationMemory`の戻り値はARNである。[更新要件と戻り値](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-memory.html#aws-resource-bedrockagentcore-memory-return-values)

## Pythonパッケージと保守状況

### 調査時点の配布版

PyPIのJSON APIから現行バージョンと配布日時を取得し、対応するwheelをメモリ上で展開して`METADATA`とPythonソースを確認した。
インストールやAWS APIの呼び出しは行っていない。
表の配布日はUTCである。[strands-agentsの配布情報](https://pypi.org/pypi/strands-agents/json)、[bedrock-agentcoreの配布情報](https://pypi.org/pypi/bedrock-agentcore/json)

| 用途 | パッケージ | バージョン | 配布日 | Python要件 |
| --- | --- | --- | --- | --- |
| Strands Agents本体 | `strands-agents` | `1.54.0` | 2026-08-27 | `>=3.10` |
| AgentCore SDKとSession Manager | `bedrock-agentcore` | `1.22.0` | 2026-08-18 | `>=3.10` |

連携クラスはAWSの`aws/bedrock-agentcore-sdk-python`で提供され、`bedrock-agentcore`のwheelに含まれる。
import先は`bedrock_agentcore.memory.integrations.strands.session_manager.AgentCoreMemorySessionManager`である。
`bedrock-agentcore[strands-agents]`の角括弧は追加依存を選ぶextraであり、Session Managerのバージョンは`bedrock-agentcore`と共通である。[配布版1.22.0](https://pypi.org/project/bedrock-agentcore/1.22.0/)、[連携ソース](https://github.com/aws/bedrock-agentcore-sdk-python/blob/v1.22.0/src/bedrock_agentcore/memory/integrations/strands/session_manager.py)

配布物の依存宣言では、このextraが`strands-agents>=1.46.0`を要求する。
AgentCore SDKは`boto3>=1.43.72`と`botocore>=1.43.72`を要求する。
Strands側の宣言はそれぞれ`boto3>=1.26.0,<2.0.0`と`botocore>=1.29.0,<2.0.0`であり、宣言上の範囲は重なる。
この確認は実行時の互換性を保証するものではない。[AgentCoreのバージョン別メタデータ](https://pypi.org/pypi/bedrock-agentcore/1.22.0/json)、[Strandsのバージョン別メタデータ](https://pypi.org/pypi/strands-agents/1.54.0/json)

確認したwheelのSHA-256を以下に示す。

| 配布物 | SHA-256 |
| --- | --- |
| `strands_agents-1.54.0-py3-none-any.whl` | `ca37b9001531596a634e9249f97fb03ce70997e216f6a71d4e8f8182818cbf5c` |
| `bedrock_agentcore-1.22.0-py3-none-any.whl` | `b4906b9c2a290e54eccd13bcf99191b890d27eedd3aa28ba756aff61d83a4948` |

### 保守主体とサポート範囲

Strandsの公式連携ページは現在もCommunity Contributionと表示し、Strandsチームの所有およびサポート対象外としている。
計画の想定は現在の記載と一致する。[AgentCore Memory Session Manager](https://strandsagents.com/docs/integrations/session-managers/agentcore-memory/)

提供元のAWSリポジトリはアーカイブされておらず、2026年8月18日に1.22.0をリリースしている。
連携部分でも2026年7月9日にイベント順序の修正、8月5日にMemory Store連携の追加があり、継続した変更を確認できる。[リポジトリ情報](https://api.github.com/repos/aws/bedrock-agentcore-sdk-python)、[1.22.0リリース](https://github.com/aws/bedrock-agentcore-sdk-python/releases/tag/v1.22.0)、[イベント順序の修正](https://github.com/aws/bedrock-agentcore-sdk-python/commit/a271ab4616f4a221d851b8146eb188cae0e60b9b)、[Memory Store連携の追加](https://github.com/aws/bedrock-agentcore-sdk-python/commit/439d788abf1d773fa1e40ea58bf53f943aea1956)

一方、配布物のDevelopment Statusは`bedrock-agentcore`が`3 - Alpha`、`strands-agents`が`5 - Production/Stable`である。
これはパッケージの自己申告分類であり、AWSサービス自体の提供段階とは区別する。
更新実績を根拠に保守が続いていると評価できるが、Strands本体と同じサポート範囲とは扱えない。[AgentCoreの配布情報](https://pypi.org/project/bedrock-agentcore/1.22.0/)、[Strandsの配布情報](https://pypi.org/project/strands-agents/1.54.0/)

## 短期記憶30日と長期記憶の抽出なし

`EventExpiryDuration: 30`と`MemoryStrategies`の省略で、両方の条件を満たせる。
AWSは戦略を定義しなければ生イベントの短期記憶だけを保存すると明記している。[Create an AgentCore Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-create-a-memory-store.html)

配布版の`MemoryClient.create_memory`も、`strategies=None`を空配列へ変換して送信する実装になっている。
保持日数は`event_expiry_days`から`eventExpiryDuration`へ渡される。
このSDKヘルパーの既定値は90日であるため、30日は明示する必要がある。[MemoryClientの実装](https://github.com/aws/bedrock-agentcore-sdk-python/blob/v1.22.0/src/bedrock_agentcore/memory/client.py)

Session Managerの設定は`retrieval_config=None`、`batch_size=1`が既定値である。
長期記憶の検索を設定しないことと、Memory側の抽出戦略を省略することは別の設定である。
抽出を止める条件はMemoryリソース側で満たす。[AgentCoreMemoryConfigの実装](https://github.com/aws/bedrock-agentcore-sdk-python/blob/v1.22.0/src/bedrock_agentcore/memory/integrations/strands/config.py)

保持期間はイベントの書き込み時に適用される。
設定変更は変更後のイベントにだけ作用し、既存イベントの期限は延長できない。
したがって30日は各イベントの保持期間として扱い、最終発言からスレッド全体を30日保持する設定とは区別する。[保持期間の適用規則](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-create-a-memory-store.html)

## Terraformでの定義

### AWS providerの専用リソース

`hashicorp/aws`には`aws_bedrockagentcore_memory`がある。
抽出戦略の`aws_bedrockagentcore_memory_strategy`とともに、2025年10月23日の6.18.0で追加された。
6.18.0未満ではこの専用リソースを使用できない。[6.18.0リリース](https://github.com/hashicorp/terraform-provider-aws/releases/tag/v6.18.0)

Memory本体の必須引数は`name`と`event_expiry_duration`である。
30日の保持は`event_expiry_duration = 30`で指定する。
抽出戦略は別の`aws_bedrockagentcore_memory_strategy`で管理し、`memory_id`でMemoryと関連付け、`type`で抽出方式を選ぶ。
抽出なしの新規Memoryでは戦略リソースを作成しない。[Memoryの引数](https://registry.terraform.io/providers/hashicorp/aws/6.62.0/docs/resources/bedrockagentcore_memory)、[抽出戦略の引数](https://registry.terraform.io/providers/hashicorp/aws/6.62.0/docs/resources/bedrockagentcore_memory_strategy)

```hcl
resource "aws_bedrockagentcore_memory" "conversation" {
  name                  = "VerifyAgentCoreMemory"
  event_expiry_duration = 30
}
```

参考構成がロックしている6.47.0では、保持期間のバリデーションが7〜365日になっている。
CloudFormationの3〜365日と下限が異なるが、30日の要件には影響しない。[6.47.0のスキーマと実装](https://github.com/hashicorp/terraform-provider-aws/blob/v6.47.0/internal/service/bedrockagentcore/memory.go)

作成、読み取り、更新、削除の実装があり、保持期間と説明は更新、名前と暗号鍵の変更は置換として扱う。
読み取りでは`GetMemory`の結果をstateへ反映するため、管理対象属性の外部変更とMemory自体の外部削除を検知できる。
Memory IDを指定したimportも提供される。[ライフサイクル実装](https://github.com/hashicorp/terraform-provider-aws/blob/v6.47.0/internal/service/bedrockagentcore/memory.go)、[import仕様](https://registry.terraform.io/providers/hashicorp/aws/6.47.0/docs/resources/bedrockagentcore_memory#import)

ただし、Memory本体のstateには抽出戦略の一覧が含まれない。
そのため、コンソールなどから追加された未管理の戦略を、Memory本体のplanだけで検知して削除することはできない。
「抽出なし」の確認には実サービスの戦略一覧の照合も必要になる。
既存の戦略を管理する場合は、戦略リソースをMemory IDとStrategy IDの組で別途importする。[Memoryのstateモデル](https://github.com/hashicorp/terraform-provider-aws/blob/v6.47.0/internal/service/bedrockagentcore/memory.go)、[戦略のimport](https://registry.terraform.io/providers/hashicorp/aws/6.62.0/docs/resources/bedrockagentcore_memory_strategy#import)

### AWSCC providerの代替経路

`hashicorp/awscc`にも`awscc_bedrockagentcore_memory`がある。
調査時点の1.100.0で、必須の`name`と`event_expiry_duration`、任意の`memory_strategies`を確認した。
30日は`event_expiry_duration = 30`、抽出なしの新規作成は`memory_strategies`の省略で表現できる。[1.100.0のリソース資料](https://github.com/hashicorp/terraform-provider-awscc/blob/v1.100.0/docs/resources/bedrockagentcore_memory.md)

Cloud Control APIを通じた作成、読み取り、更新、削除と、Memory ARNによるimportに対応する。
読み取り結果をTerraformの属性へ変換してstateを更新するため、設定した属性についてdriftを検知できる。[共通ライフサイクル実装](https://github.com/hashicorp/terraform-provider-awscc/blob/v1.100.0/internal/generic/resource.go)

`memory_strategies`は`Optional`かつ`Computed`である。
省略時はサービス側の値を受け入れるため、外部からの戦略追加を自動的に取り消す設定にはならない。
空配列を明示して戦略ゼロを管理できるかは、API応答とproviderの正規化を含めた実機確認が必要である。[AWSCCのMemoryスキーマ](https://github.com/hashicorp/terraform-provider-awscc/blob/v1.100.0/internal/aws/bedrockagentcore/memory_resource_gen.go)

### Cloud Control APIの汎用リソース

`aws_cloudcontrolapi_resource`に`type_name = "AWS::BedrockAgentCore::Memory"`を指定し、`desired_state`へ`Name`と`EventExpiryDuration`をJSONで渡す経路も定義可能である。
AWSの対応表では、この型のCreate、Read、Update、Delete、Listがすべて対応している。[Cloud Control API対応表](https://docs.aws.amazon.com/cloudcontrolapi/latest/userguide/supported-resources.html)、[汎用リソースの資料](https://registry.terraform.io/providers/hashicorp/aws/6.62.0/docs/resources/cloudcontrolapi_resource)

ただし、確認したAWS provider 6.62.0の汎用実装にはImporterがなく、通常のimportに対応していない。
Readは実際の値を計算属性`properties`へ保存するだけで、設定属性`desired_state`へ反映しない。
Updateは変更前後の`desired_state`の差からパッチを作るため、外部変更を設定との差として自動修復する通常のdrift管理には使えない。
外部削除の検知と実際の値の取得はできるが、専用リソースと同等の管理機能は揃わない。[6.62.0の汎用リソース実装](https://github.com/hashicorp/terraform-provider-aws/blob/v6.62.0/internal/service/cloudcontrol/resource.go)

### デプロイ構成

TerraformでMemory、Lambda、実行ロールとロググループを管理し、lambrollでLambdaのコードと実行設定を配布する。
`deploy-infra`と`deploy-app`を分け、関数名、ロールARN、Memory IDをTerraform outputから渡す。
AWS providerの制約は`>= 6.18.0, < 7.0.0`とし、対応範囲の下限と上限を明示する。
lockファイルはその範囲内で使用する1バージョンを固定する。

### Terraform経路についての所見

AWS providerの専用リソースで検証を進められるというのが、資料と実装からの評価である。
AWSCCも代替候補になるが、汎用Cloud Control APIリソースはimportとdrift管理の不足があるため、同等の代替とは扱えない。
現時点でTerraform対応の不足を理由にMemory以外の保存方式へ切り替える必要は認められない。

各経路の東京での作成、更新、削除、import、外部変更後のplanは未実施である。
専用リソースで問題が生じた場合は、その原因がAWS provider固有かを確認してAWSCCを評価する。
Memory自体の保存や復元が要件を満たさない場合は、S3SessionManagerを含む保存方式の再検討が必要になる。
provider経路の決定と実機検証後のバージョン固定は未完了である。

## 保存方式についての所見

提供リージョン、IaC定義、保持設定、連携パッケージの存在という調査項目には、AgentCore Memoryでの検証を妨げる制約は見つからなかった。
実機検証でも復元、分離、権限、タイムアウトが要件どおりに動いたため、保存方式はAgentCore Memoryで確定する。
採用バージョンは`strands-agents 1.54.0`、`bedrock-agentcore 1.22.0`、`boto3`と`botocore`の1.43.89とする。

強制終了時の欠落は保存の単位に依存する。
配布版はバッファを`close()`またはコンテキストマネージャーの終了時にflushする実装であり、強制終了時にこの処理へ到達する保証としては使えない。[保存と終了処理の実装](https://github.com/aws/bedrock-agentcore-sdk-python/blob/v1.22.0/src/bedrock_agentcore/memory/integrations/strands/session_manager.py)
イベントごとに保存する構成では、強制終了しても直前のユーザーの発言までは残る。
ワーカーの実装ではイベントごとの保存を選び、失われた応答の扱いは呼び出し側の再開処理で決める。

S3SessionManagerへの切り替えが必要になった場合は、`strands-agents 1.54.0`のwheel内の`strands/session/s3_session_manager.py`を使う。[S3SessionManagerの公式API資料](https://strandsagents.com/docs/api/python/strands.session.s3_session_manager/)
切り替える場合は、保存先の権限、暗号化、公開アクセスのブロック、30日のライフサイクルを決め、ADRと計画を改訂する。

## 実機検証の結果

### 作成と更新の再現

`terraform apply`と再applyのどちらも同じoutputを返し、構成の再現を確認した。
lambrollで関数コードを配布した後の`terraform plan`は差分なしとなり、コード配布はTerraformのdriftとして現れない。

### 保存と復元

同一のMemory ID、actor ID、session IDで、1往復目と2往復目の履歴をこの順で復元できた。
別のsession IDでは履歴が復元されず、セッション間が分離されることを確認した。

### 権限とタイムアウト

実行ロールに与えていない操作は`AccessDeniedException`で拒否された。
データプレーンの呼び出しが応答しない場合は`ReadTimeoutError`として検出でき、Lambdaのタイムアウトを待たずに扱える。

### セッションの途中終了

Lambdaの実行時間切れ（`Sandbox.Timedout`）を起こし、保存済みのイベントだけが残ることを確認した。

| 保存の単位 | 途中終了後に復元できたもの |
| --- | --- |
| イベントごとに保存 | 直前に送信したユーザーの発言のみ。応答は失われる |
| 100件をまとめて保存 | なし。flushへ到達しない |
| 例外で終了 | 直前に送信したユーザーの発言のみ |

まとめて保存する構成でも、バッチを明示的に閉じた場合は往復が保存された。
途中終了したセッションを再実行するときは、保存済みイベントとの重複を呼び出し側で判定する必要がある。

### 削除

`terraform destroy`で5つのリソースを削除し、Memory、Lambda関数、IAMロール、ロググループのいずれも残っていないことをAWS CLIで確認した。

## 実機検証の構成と再現手順

検証用LambdaはPython 3.12、arm64、512 MiB、タイムアウト20秒で実行する。
Strandsのモデルは固定テキストを返す検証用実装であり、会話の保存先は東京のAgentCore Memoryである。
Bedrockのモデル推論とtool useの互換性は、この検証の対象に含まれない。

バックエンド定義はS3の暗号化とnative S3 lockingを有効にし、バケット名をgit管理外の`terraform/backend.tfbackend`から渡す。
今回の検証はローカルstateを使用し、state用バケットは作成しない。
ローカルstateで再現する場合は、git管理外の`terraform/backend_override.tf`を次の内容で作成する。

```hcl
terraform {
  backend "local" {}
}
```

S3バックエンドを使用する場合は、このoverrideを置かず、既存バケットを次のファイルで指定する。

```hcl
bucket = "<state-bucket-name>"
```

次のコマンドは検証専用のAWS環境で実行する。
`make verify`はLambdaを呼び出してイベントを保存し、タイムアウトも発生させる。

```sh
mise exec terraform@1.16.0 aqua:fujiwara/lambroll@1.5.2 -- make deploy-infra
mise exec terraform@1.16.0 aqua:fujiwara/lambroll@1.5.2 -- make deploy-app
uv venv .verification/venv --python 3.12
uv pip install --python .verification/venv/bin/python -r app/requirements.txt "botocore[crt]==1.43.89"
.verification/venv/bin/python scripts/verify_memory.py
mise exec terraform@1.16.0 -- terraform -chdir=terraform plan
mise exec terraform@1.16.0 -- make destroy
```

Lambdaの実行ロールは対象Memoryの`CreateEvent`、`GetEvent`、`ListEvents`と対象ロググループへの書き込みを許可する。
モデル呼び出し、長期記憶の検索、Memoryの管理操作、イベント削除の権限は付与しない。
この権限は新規セッションの検証用であり、既存形式の移行やメッセージ改訂で必要になる削除権限は別途評価する。
