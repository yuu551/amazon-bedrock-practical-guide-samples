# 章ごとの前提と引き継ぎ

実行するコマンドは、各章のREADME（`sample-code/<章>/README.md`）に書かれています。ここでは、章をまたいで迷いやすい点だけをまとめます。

## 第1章

- 作るもの：Guardrail（`setup_guardrail.py`）。weatherDemoをデプロイすると、AgentCore RuntimeとMemory
- 引き継ぐ値：`GUARDRAIL_ID`（`setup_guardrail.py`が表示）
- 注意：新しいモデルは、アカウントによって制限があり、`AccessDeniedException`と`not available for this account`で止まる。必須のモデルは、Sonnet 4.6、Haiku 4.5、Titan Text Embeddings V2。それ以外は飛ばして進められる
- weatherDemo：本文と同じ`@aws/agentcore@0.28.1`を使う。同梱のプロジェクトをそのまま使い、`agentcore create`は実行しない。`agentcore dev`にはDockerが必要

## 第2章

- 作るもの：Knowledge Base。OpenSearch Serverless版（`infra/setup.sh`）は、検索していなくてもOCUの料金がかかる。S3 Vectors版（`infra/setup-s3vectors.sh`）は、常時の費用がない
- 引き継ぐ値：`setup.sh`の最後に表示される`export`の行（`KNOWLEDGE_BASE_ID`、`DATA_SOURCE_ID`、`MODEL_ARN`、`MODEL_ID`）。新しいターミナルでは、実行し直す。表示を見失った場合は、`infra/.env`（S3 Vectors版は`infra/.env.s3vectors`）に値がある
- 画面：`sample-code/02/amplify-ui`で、`KNOWLEDGE_BASE_ID`と`MODEL_ID`を渡して`pnpm exec ampx sandbox`。`amplify_outputs.json`ができたら、別のターミナルで`pnpm run dev`
- 後続の章は、この章のKnowledge Baseを使う

## 第3章

- 前提：第2章のKnowledge Baseと、取り込み済みの資料
- 作るもの：比較用のKnowledge Base（`infra/setup-chunking-kbs.sh`など、それぞれOpenSearch Serverlessのコレクションを作る）。RAGポータル（`amplify-ui`のsandbox）
- 引き継ぐ値：RAGポータルのFunction URL。`amplify-ui`で`node -p "require('./amplify_outputs.json').custom.ragFunctionUrl"`を実行して取り出し、第5章の`APP_API_URL`にする
- 注意：第2章をS3 Vectors版で作った場合、ハイブリッド検索は使えない（`compare_rag_configs.py --vector-store s3vectors`）

## 第4章

- 前提：第2章のKnowledge Base。Atlassianのサイト（ConfluenceとJira）と、APIトークン
- 作るもの：`sample-code/04/amplify-ui`のsandbox（AgentCore Runtime、Gateway、Memory、Lambda、Secrets Managerのシークレット）。シークレットは保管中、月額の料金がかかる
- 引き継ぐ値：`amplify_outputs.json`の`custom.gatewayUrl`（第5章の`GATEWAY_URL`）、`custom.agentCoreRuntimeArn`、`custom.memoryId`
- Secrets Managerの`support-agent/atlassian`への登録は、読者自身が行う
- ローカルCLI（`run_gateway_agent.py`）は、`GATEWAY_URL`なしでも、Knowledge Baseの検索だけで動く

## 第5章

- 作るもの：第5章だけのAWSリソースはない
- 前提：評価するAPI。第3章のRAGポータルの`APP_API_URL`、または第2章の画面のAPI。モデルは、`JUDGE_MODEL_ID`、`FEEDBACK_MODEL_ID`など
- 評価結果は、`output/`とExcelの結果シートに書き込まれる。Excelを元に戻すときは、`git checkout -- data agent_data`
- 注意：RAGASの非推奨の警告は、ロックファイルで固定したバージョンでは出るが、本文と同じ指標で採点できる

## 第6章

- 作るもの：Amplify版（`sample-code/06/amplify-ui`、`pnpm sandbox`）と、CDK版（`sample-code/06/cdk`、`SupportAiDev`、`SupportAiProd`、`KbUpdateAgentDev`）
- CDK版は、初回に`pnpm exec cdk bootstrap`が必要。`MODEL_SOURCE_ARN`も必要。`SupportAiProd`は任意で、同じ構成がもう1つ増える
- Amplify版のsandboxの識別子は`ch06-private`。削除は`pnpm sandbox:delete`
- `agent-registry`は、`uv run python register_and_search_registry.py demo --yes --region us-east-1`で、レジストリとレコードを作り、登録・承認・検索を試せる。AWS上にレジストリが作られるので、終わったら削除する

## 付録

- S3 Vectors（`s3_vectors_standalone.py`）：`setup`でベクトルバケットを作る。残すと保管料がかかり続けるので、`cleanup`で削除する
- Managed Knowledge Bases：比較（`managed_kb_comparison.py`）には、先にKnowledge Baseが必要。横断検索（`managed_kb_cross_search.py --create`）は、Knowledge Baseを作る。`--cleanup`を付けると、完了後に削除される
