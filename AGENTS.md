# ハンズオンの案内役

このリポジトリは『Amazon Bedrock実践ガイド』のサンプルコードです。ハンズオンを手伝うときは、スキル`bedrock-handson`（`.agents/skills/bedrock-handson/SKILL.md`）の手順に従ってください。スキルを読み込めないエージェントは、`SKILL.md`を直接読んでください。

答える前に、このリポジトリの文書を読みます。READMEに書かれていない手順は推測で補わず、「READMEには記載がありません」と伝えます。

## 必ず守ること

- AWSにリソースを作り料金が発生するコマンド（`setup*.sh`、`spin-up-*.sh`、`ampx sandbox`、`cdk deploy`、`agentcore deploy`、`--create`付きのスクリプト）は、何が作られて何に料金がかかるかを伝え、読者の了承を得てから実行する
- 削除のコマンドも、対象を示して了承を得てから実行する
- アクセスキー、APIトークン、Atlassianのトークンをファイルやコマンドの引数に書き込まない。Secrets Managerへの登録は、READMEのコマンドを読者自身に実行してもらう
- 依存パッケージは`uv sync --frozen`と`pnpm install --frozen-lockfile`で入れ、読者に頼まれない限りバージョンを上げない
- 名前から判断できないAWSリソースは削除しない
