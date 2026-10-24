# retention01: 運用指標ダッシュボードの棒グラフで、Enterpriseプランの監査ログ保持期間は何日と記載されていますか

## default_parser

- Latency: 4316.4 ms
- Citation count: 2
- Expected source hit: True

Answer:

申し訳ありませんが、提供された検索結果には、Enterpriseプランの監査ログ保持期間の具体的な数値は記載されていません。検索結果によると、各プランの具体的な保持日数はグラフの棒の上部ラベルに記載されているとのことですが、そのラベルの数値自体は文書内に含まれておらず、確認することができません。

Retrieved references:

### 1. s3://ch03-parser-sample-shot822d/sample-pdfs/sampleconnect-retention-chart.pdf

```text
SampleConnect プラン別 運用指標ダッシュボード   本書は、営業・導入検討用に公開している運用指標の抜粋です。監査ログ保持期間とAPIレート制限   は、プランによって桁が異なるためログスケールで表示しています。数値は各棒の上部ラベルに記   載しており、本文中には具体値を繰り返し記載していません。   図2. 左：監査ログ保持期間の比較。右：APIレート制限の比較。どちらも縦軸は対数スケールで、ラベルにプランご   との値が記載されています。   読み解きのポイント   保持期間の差は、監査要件の厳しさに応じてプランを選ぶ際の主要な判断材料になります。
```

### 2. s3://ch03-parser-sample-shot822d/sample-pdfs/sampleconnect-retention-chart.pdf

```text
読み解きのポイント   保持期間の差は、監査要件の厳しさに応じてプランを選ぶ際の主要な判断材料になります。上位プ   ランほど長期保持を前提に設計されており、各プランでの具体的な保持日数は上の左側のグラフの   ラベルを参照してください。   APIレート制限については、バッチ処理や大規模連携を行う場合に上位プランの上限が必要になるケ   ースが多く、下位プランではピーク時に制限に達する事例が報告されています。各プランの上限値   は上の右側のグラフのラベルを参照してください。   補足情報   本資料の数値は2026年4月時点のものです。
```


## fm_parser

- Latency: 2223.8 ms
- Citation count: 1
- Expected source hit: True

Answer:

Enterpriseプランの監査ログ保持期間は730日と記載されています。

Retrieved references:

### 1. s3://ch03-parser-sample-shot822d/sample-pdfs/sampleconnect-retention-chart.pdf

```text
## グラフ要素の説明 ### SampleConnect Platform プラン別 運用指標（2026年4月時点） **左側グラフ：監査ログ保持期間（日）** - 縦軸：保持日数（対数スケール） - プラン別の値： - Free: 30日 - Standard: 90日 - Enterprise: 730日 - 各プランの保持期間を比較 **右側グラフ：APIレート制限（req/sec）** - 縦軸：レート制限（対数スケール） - プラン別の値： - Free: 10 req/sec - Standard: 100 req/sec - Enterprise: 1,000 req/sec - 各プランのAPI呼び出し上限を比較 図2. 左：監査ログ保持期間の比較。右：APIレート制限の比較。どちらも縦軸は対数スケールで、ラベルにプランごとの値が記載されています。
```
