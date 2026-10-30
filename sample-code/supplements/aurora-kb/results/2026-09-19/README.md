# Auroraの検索評価（2026年9月19日）

第3章の18問を4構成で検索した72件の結果です。`evaluation.json`に質問・フィルタ・検索順位・採点結果、`evaluation-summary.json`に集計値と環境を記録しています。

質問011の期待文書を課金請求ポリシー1件にそろえ、保存済みの検索結果を再採点しました。質問文・フィルタ・検索結果は変えていません。検索の実行日時と再採点日時、変更前後の質問ファイルのSHA-256を集計ファイルに記録しています。

## 3ストアの比較

`cross-store-evaluation.json`と`cross-store-summary.json`は、Auroraのフィルタなし2構成に、付録のOpenSearch・S3 Vectorsの検索結果を加えた集計です。付録の23問から共通の18問を取り出し、現在の期待文書で同じ採点関数を適用しています。

比較元は`sample-code/appendix/logs/20260825-answer-quality-comparison/s3vectors-opensearch/comparison-results.json`です。33文書と33メタデータファイルの内容が現在のサンプルと一致することを、保存されているETagとローカルファイルのMD5で確認しています。追加の5問、生成回答の品質スコア、フィルタありの結果はこの比較に含めていません。

検索はOpenSearch・S3 Vectorsが2026年8月25日、Auroraが9月19日（日本時間）です。保存済み結果の再集計であり、3ストアを同日に新たに検索した結果ではありません。所要時間はPCからのRetrieve API呼び出し時間で、ストア内部の検索時間ではありません。

`cross-store-summary.json`には質問と元ログのSHA-256、照合したファイル数、各文書・メタデータのSHA-256も保存しています。

再集計:

```bash
uv run python compare_saved_results.py
```

費用表は、2026年9月19日に確認した公式単価からの概算です。Auroraは$0.12/ACU時、OpenSearch Serverless Classicは$0.24/OCU時、S3 Vectorsのリクエストは$2.50/100万回。1か月730時間とし、計算資源と従量課金の対象を補足本文に書き分けています。実際の請求額を測定した表ではありません。
