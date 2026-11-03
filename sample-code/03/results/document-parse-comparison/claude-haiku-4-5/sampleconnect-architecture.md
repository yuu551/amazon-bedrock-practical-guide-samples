# SampleConnect RAG 参考アーキテクチャ

本資料では、SampleConnectの問い合わせ対応を支えるRAG基盤の構成要素と、主要なデータフローをまとめます。図で全体像を示したうえで、各コンポーネントの役割を補足します。

## 図1. SampleConnect RAG 参考アーキテクチャ

ユーザー入力はAPI Gatewayを経由してKnowledge Basesに渡り、OpenSearch Serverlessでベクトル検索、Bedrock基盤モデルで埋め込みと回答生成を行います。

### アーキテクチャの流れ

- ユーザー入力
- 認証 → API Gateway
- Retrieve → Knowledge Bases
- チャンク検索 ↑
- トリガー → Lambda関数
- ベクトル検索 → OpenSearch Serverless
- 埋め込み ↓
- アップロード → S3（文書格納）
- メタデータ生成 → メタデータ出力
- Bedrock基盤モデル

## コンポーネントの役割

| コンポーネント | 役割 | 補足 |
|---|---|---|
| API Gateway | 認証済みの質問を受け付ける入口 | Cognito連携によりユーザー属性を付与 |
| Lambda関数 | リクエスト整形とKB呼び出し | audience属性に応じてフィルタを切り替え |
| Knowledge Bases | 検索・回答生成の統合 | RetrieveAndGenerate APIを使用 |
| OpenSearch Serverless | ベクトルインデックスの格納 | VECTORSEARCH コレクション |
| S3 | 原本ドキュメントの格納 | .metadata.jsonサイドカーを同梱 |
| メタデータ生成Lambda | サイドカーの自動生成 | Structured Output方式を優先 |
| Bedrock基盤モデル | 埋め込み・応答生成・メタデータ抽出 | Titan Embed v2 + Claude系 |

## コンポーネントの役割

| コンポーネント | 役割 | 補足 |
|---|---|---|
| API Gateway | 認証済みの質問を受け付ける入口 | Cognito連携によりユーザー属性を付与 |
| Lambda関数 | リクエスト整形とKB呼び出し | audience属性に応じてフィルタを切り替え |
| Knowledge Bases | 検索・回答生成の統合 | RetrieveAndGenerate APIを使用 |
| OpenSearch Serverless | ベクトルインデックスの格納 | VECTORSEARCH コレクション |
| S3 | 原本ドキュメントの格納 | .metadata.jsonサイドカーを同梱 |
| メタデータ生成Lambda | サイドカーの自動生成 | Structured Output方式を優先 |
| Bedrock基盤モデル | 埋め込み・応答生成・メタデータ抽出 | Titan Embed v2 + Claude系 |
