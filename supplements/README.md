# オンライン補足

本で作ったアプリをもとにモデルを変えたり検索方法を工夫したりする記事をまとめています。

本書の執筆後も試してみたいモデルや機能が増えています。本編のサンプルで試した結果や紙面に収まらなかった手順をこちらにまとめていきます。気になる記事から読んでみてください。

補足を読んで本編も気になった方はぜひ『Amazon Bedrock実践ガイド』も手に取っていただけるとうれしいです。本編では問い合わせ対応のアプリを作りながらRAGやAIエージェントの実装を順に学べます。

## 実行前に確認すること

Codespacesの無料枠とAWSの費用の考え方はサンプルコードのREADMEの[費用の確認](../sample-code/README.md#費用の確認)にまとめています。

## 作ったアプリを公開する

[Amplifyコンソールからアプリを公開する](chapter02/Amplifyコンソールからアプリを公開する.md)では第2・3・4・6章のアプリをGitHubからビルドし、画面とバックエンドをAmplifyへデプロイします。

## モデルとエージェントを試す

| 関連する章 | 記事 | 内容 |
|---|---|---|
| 第1章 | [本編のコードで最新モデルを使う](chapter01/本編のコードで最新モデルを使う.md) | Sonnet 5.5・Opus 5.5・GPT-6.1 Solなどを本編のコードで使う。設定・effort・推論先と料金・Knowledge Basesとの組み合わせ |
| 第1章 | [AgentCore CLI 1.0.0で第1章の手順を進める](chapter01/AgentCore-CLI-1.0.0で第1章の手順を進める.md) | 最新版（1.0.0）での変更点。ウィザードとinvokeの読み替え、本書のバージョンのまま進める方法 |
| 第1章 | [コンテキストグラウンディングチェックにStandardティアが追加された](chapter01/コンテキストグラウンディングチェックにStandardティアが追加された.md) | Standardティアの追加で変わる対応言語と会話調の回答。評価結果・複数ターンの会話での渡し方・設定のしかた |
| 第4章 | [ツール・エージェント・モデルの入口をAgentCore Gatewayで統制する](chapter04/ツール・エージェント・モデルの入口をAgentCore-Gatewayで統制する.md) | ツールに加えてエージェントやモデルの接続先を管理する |
| 第4章 | [第4章のアプリをAmplifyからCDKへ移行する](chapter04/第4章のアプリをAmplifyからCDKへ移行する.md) | 画面をS3とCloudFrontへ移してCDKでデプロイし、Amplifyとデプロイ時間を比べる。cdkd・GitHub Actions・Lambda Web Functionsも試す |
| 第5章 | [RAGASでエージェントのツール呼び出しと目的達成を評価する](chapter05/RAGASでエージェントのツール呼び出しと目的達成を評価する.md) | エージェントの実行履歴を使って採点する |
| 第6章 | [エージェントとスキルをAWS Agent Registryで組織管理する](chapter06/エージェントとスキルをAWS-Agent-Registryで組織管理する.md) | AWS Agent Registryで登録・承認・更新を試す |
| 第4・5・6章 | [CloudWatch Omniでエージェントを調べて改善する](chapter06/CloudWatch-Omniでエージェントを調べて改善する.md) | 第4章のエージェントを題材に実トレースの評価とプロンプト比較を試す |
| 第6章 | [BedrockのコストをCost Explorerでモデル別に見る](chapter06/BedrockのコストをCost-Explorerでモデル別に見る.md) | モデル・プロバイダー・推論タイプ・機能でコストを分ける。コンソールとAPIでの見方 |

## 資料の解析と検索を試す

| 関連する章 | 記事 | 内容 |
|---|---|---|
| 第2章 | [AuroraでKnowledge Basesの検索を検証する](chapter02/AuroraでKnowledge-Basesの検索を検証する.md) | テーブルの接続と文書取り込みの実装・18問での比較・日本語の全文検索とpg_bigm・保存先ごとの連携と費用の違い |
| 第2章 | [OpenSearch Serverless NextGenをKnowledge Basesで使えるか試す](chapter02/OpenSearch-Serverless-NextGenをKnowledge-Basesで使えるか試す.md) | Knowledge Basesに接続できない理由と直接検索・ハイブリッド検索の実装 |
| 第3章 | [生成AIでPDFを解析して取り込む](chapter03/生成AIでPDFを解析して取り込む.md) | 表や図を含むPDFをマークダウンへ変換する |
| 第3章 | [第3章のRAGアプリで回答の過程を可視化する](chapter03/第3章のRAGアプリで回答の過程を可視化する.md) | LangfuseとAgentCore Observabilityで質問・中間回答・引用元をたどる |
| 付録 | [Managed Knowledge Baseをアプリに組み込んでストリーミングする](managed-knowledge-bases/Managed-Knowledge-Baseをアプリに組み込んでストリーミングする.md) | 本編と同じ画面で通常検索とAgentic Retrievalを試す |
| 付録 | [データソースを定期同期する](managed-knowledge-bases/Managed-Knowledge-Baseのデータソースを定期同期する.md) | Managed Knowledge Baseに日次・週次・月次の同期を設定する |
| 付録 | [S3 Vectorsのメタデータフィルタが事前フィルタリングに対応](s3-vectors/S3-Vectorsのメタデータフィルタが事前フィルタリングに対応.md) | 既存インデックスのモード確認と切り替え・`$startsWith`と条件数の上限 |

## 本文で抜粋した回答を読む

- [第3章：部門フィルタの有無による回答全文](chapter03/部門フィルタの有無による回答全文.md)
- [付録：Managed Knowledge Baseで生成された回答の全文](managed-knowledge-bases/生成された回答の全文.md)

[サポートリポジトリのトップに戻る](../README.md)
