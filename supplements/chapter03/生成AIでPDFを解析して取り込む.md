# 生成AIでPDFを解析して取り込む

第3章の本文では図表を含むPDFの読み取りにKnowledge Basesの基盤モデルパーサーを使いました。変換結果を自分で確認・修正してから取り込みたい場合はConverse APIでPDFをマークダウンに変換し、S3へ配置する方法も使えます。

## どういうときに向いているか

変換結果を確認・修正してから取り込みたい場合など、次のようなケースではConverse APIで事前にマークダウンへ変換する方法が向いています。


| 状況                             | 自前変換が向く理由                   |
| ------------------------------ | --------------------------- |
| 変換結果を人が検収・修正してから投入したい          | マークダウンを確認・手直ししてからS3へ配置できる   |
| 文書の種類ごとに抽出の指示を変えたい             | プロンプトを文書ごとに変えられる            |
| Knowledge Bases以外の検索基盤にも使い回したい | 変換結果がBedrockに依存しないテキストになる   |
| 第2章のようにマークダウン前提で運用したい          | 変換済みマークダウンがそのまま既存の取り込み手順に乗る |


メリットもある反面、変換ジョブの実行や失敗時の再実行は自前で管理します。取り込みのたびに自動で処理してほしい場合は基盤モデルパーサーの方が運用は楽といったトレードオフな関係です。

## 仕組み

Converse APIは`document`ブロックでファイルのバイト列をそのままモデルへ渡せます。対応する形式はPDF・Word・Excel・テキスト系などで、PowerPointは対象外です。そのためPPTXなどのファイルはPDFへ変換してから渡します。

スクリプトの処理は次の3つです。

1. 対象ディレクトリのPDFを1件ずつ読み込む
2. 抽出指示のプロンプトと一緒にConverse APIへ送り、マークダウンのテキストを受け取る
3. 元のファイル名で`.md`を出力ディレクトリへ保存する

抽出の指示は本文の基盤モデルパーサーで使ったプロンプトと同じ考え方です。表は列見出しとセルの対応を保ってマークダウンの表にし、図やグラフはラベルと数値を箇条書きにします。「画像から読み取れた文字だけを残し、推測で補完しない」と明示すると、原本にない内容が読み取り結果に混ざるのを抑えられます。

## 使い方

```bash
cd sample-code/03
uv sync --frozen
uv run python llm_document_parse.py \
  --input-dir sample-pdfs \
  --output-dir output/parsed-markdown
```

主なオプションは次のとおりです。


| オプション          | 用途               | デフォルト                    |
| -------------- | ---------------- | ------------------------ |
| `--input-dir`  | 変換するPDFのあるディレクトリ | `sample-pdfs`            |
| `--output-dir` | マークダウンの出力先       | `output/parsed-markdown` |
| `--model-id`   | 読み取りに使うモデル       | Claude Haiku 4.5         |
| `--force`      | 出力済みのファイルも再変換する  | オフ（出力済みはスキップ）            |
| `--workers`    | 同時に変換するPDFの数     | `1`（1件ずつ）                |


PDFが多い場合は`--workers`で並列に解析できます。スロットリングで失敗した呼び出しは待ち時間を延ばしながら再試行します。

```bash
uv run python llm_document_parse.py \
  --input-dir sample-pdfs \
  --output-dir output/parsed-markdown \
  --workers 4
```

出力されたマークダウンは第2章の手順どおりメタデータファイルと一緒にS3へアップロードし、Knowledge Baseを同期すれば検索対象になります。

## 実行結果

Claude Haiku 4.5でサンプルPDFの4件を変換しました。


| 入力PDF                               | 所要時間   | 入力トークン | 出力トークン | 確認できた内容             |
| ----------------------------------- | ------: | ------: | ------: | ------------------- |
| [sampleconnect-architecture.pdf](../../sample-code/03/sample-pdfs/sampleconnect-architecture.pdf)    | 8.58秒  | 2,238  | 901    | コンポーネント名、役割、矢印のラベル（一部誤読）  |
| [sampleconnect-incident-report.pdf](../../sample-code/03/sample-pdfs/sampleconnect-incident-report.pdf) | 27.42秒 | 7,629  | 2,670  | 発生・復旧時刻、影響範囲、タイムライン |
| [sampleconnect-plan-matrix.pdf](../../sample-code/03/sample-pdfs/sampleconnect-plan-matrix.pdf)     | 6.94秒  | 2,405  | 739    | プランごとの列と料金・機能の対応    |
| [sampleconnect-retention-chart.pdf](../../sample-code/03/sample-pdfs/sampleconnect-retention-chart.pdf) | 6.54秒  | 2,266  | 543    | 棒グラフ上の保持日数とAPI上限    |


出力は`output/parsed-markdown/`に元のファイル名で保存されます。以下はそこからの抜粋です。

料金表のPDFは列をずらさず次のようなマークダウン表へ変換できました。カテゴリが同じ行は原本どおり空欄になっています。

```markdown
| カテゴリ | 機能 | Free | Standard | Enterprise |
|---------|------|------|----------|-----------|
| 基本 | ユーザー数上限 | 5名 | 100名 | 無制限 |
| | ストレージ容量 | 5GB | 100GB | 500GB |
| | APIレート制限 | 10 req/sec | 100 req/sec | 1,000 req/sec |
| 運用 | 監査ログ保持期間 | 30日 | 90日 | 2年間 |
| | SLA（稼働率保証） | — | 99.9% | 99.95% |
| | サポート応答時間 | 72時間 | 24時間 | 4時間 |
```

棒グラフのPDFは本文に数値を書かず、値は[棒の上のラベル](../../sample-code/03/sample-pdfs/sampleconnect-retention-chart.chart.png)にだけ置いています。それでも`sampleconnect-retention-chart.md`には監査ログの保持期間とAPIレート制限の値がそろって出力されていました。

```markdown
**左：監査ログ保持期間の比較**
- 縦軸：保持日数（日）
- 横軸スケール：対数スケール
- Free：30日
- Standard：90日
- Enterprise：730日

**右：APIレート制限の比較**
- 縦軸：リクエスト上限（req/sec）
- 横軸スケール：対数スケール
- Free：10 req/sec
- Standard：100 req/sec
- Enterprise：1,000 req/sec
```

値はグラフのラベルと一致しています。ただし対数スケールなのは縦軸で、「横軸スケール」は読み違いです。

アーキテクチャ図のPDFでは[図の部分](../../sample-code/03/sample-pdfs/sampleconnect-architecture.diagram.png)が次の箇条書きになりました。

```markdown
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
```

「Retrieve」「ベクトル検索」「トリガー」「埋め込み」「アップロード」は原本の矢印ラベルと同じです。一方で原本の「認可」は「認証」に、「チャンク格納」は「チャンク検索」に変わっていました。「質問」と「メタデータ抽出」のラベルは抜け、原本にない「メタデータ出力」が増えています。矢印の向きも一部は違います。たとえば原本の「アップロード」はS3からメタデータ生成へ向かう矢印です。

インシデントレポートはタイムラインの時刻とエラーレートを原本どおり保持していました。

```markdown
- **13:05 JST**: 特定テナントの `/v1/search` で 5xx応答が断続発生。内部メトリクスでエラーレートが1.2%から18.4%まで急増。

（中略）

- **14:31 JST**: ローリング再起動完了。エラーレートが平常値（0.04%）まで低下したのを確認。

- **14:47 JST**: 全リージョンで監視ダッシュボード上のSLOを満たしたことを確認し、全面復旧を宣言。
```

アーキテクチャ図の出力にはもう1つ問題がありました。原本に1つしかない「コンポーネントの役割」の表が2回続けて出力されています。

```markdown
## コンポーネントの役割

| コンポーネント | 役割 | 補足 |
|---|---|---|
| API Gateway | 認証済みの質問を受け付ける入口 | Cognito連携によりユーザー属性を付与 |
（6行続く）

## コンポーネントの役割

| コンポーネント | 役割 | 補足 |
|---|---|---|
| API Gateway | 認証済みの質問を受け付ける入口 | Cognito連携によりユーザー属性を付与 |
```

数値は正しく読めても、図のラベルの読み違いや表の重複は出力だけ見ても気づかないケースもあるため、出力をそのまま投入せず、S3へ配置する前に原本と見比べて、数値とラベル、重複を確認してください。

## Claude Haiku 5.5で変換する

`--model-id`にClaude Haiku 5.5を渡すと同じスクリプトでHaiku 5.5に変換可能です。
東京リージョンなら`jp.`推論プロファイルを使い、スクリプトは`AWS_REGION`が未設定だと`us-east-1`を呼ぶので先頭で東京リージョンを指定します。

```bash
AWS_REGION=ap-northeast-1 uv run python llm_document_parse.py \
  --input-dir sample-pdfs \
  --output-dir output/parsed-markdown-haiku-5-5 \
  --model-id jp.anthropic.claude-haiku-5-5
```

Haiku 5.5はtemperatureを受け付けないモデルです。スクリプトはモデルIDを見てtemperatureを送らないようにしているので、ほかの指定は不要です。

同じ4件を東京リージョンでHaiku 4.5とHaiku 5.5で変換して比較してみました。

| 入力PDF | Haiku 4.5の所要時間 | Haiku 5.5の所要時間 | Haiku 5.5の入力トークン | Haiku 5.5の出力トークン |
| --- | ---: | ---: | ---: | ---: |
| [sampleconnect-architecture.pdf](../../sample-code/03/sample-pdfs/sampleconnect-architecture.pdf) | 7.70秒 | 6.25秒 | 2,316 | 1,509 |
| [sampleconnect-incident-report.pdf](../../sample-code/03/sample-pdfs/sampleconnect-incident-report.pdf) | 26.32秒 | 10.85秒 | 7,715 | 3,105 |
| [sampleconnect-plan-matrix.pdf](../../sample-code/03/sample-pdfs/sampleconnect-plan-matrix.pdf) | 6.55秒 | 4.49秒 | 2,462 | 1,055 |
| [sampleconnect-retention-chart.pdf](../../sample-code/03/sample-pdfs/sampleconnect-retention-chart.pdf) | 6.11秒 | 4.22秒 | 2,287 | 929 |

出力結果は[results/document-parse-comparison](../../sample-code/03/results/document-parse-comparison/)に配置しているのであわせてご参照ください！

Haiku 5.5ではHaiku 4.5であったアーキテクチャ図の読み違いがなくなりました。9つの矢印のラベルと向きは原本どおりに読み込めています。「コンポーネントの役割」の表も1回だけで、グラフの軸も「縦軸：保持日数（対数スケール、10¹〜10³）」と正しく読めています。

アーキテクチャ図は矢印ごとにラベルと始点・終点を組にして出力されました。

```markdown
- 矢印の流れ（ラベル: 始点 → 終点）:
  - 質問: ユーザー入力 → API Gateway
  - Retrieve: API Gateway → Knowledge Bases
  - 認可: API Gateway → Lambda関数
  - ベクトル検索: Lambda関数 → OpenSearch Serverless
  - チャンク格納: OpenSearch Serverless → Knowledge Bases
  - 埋め込み: Bedrock基盤モデル → OpenSearch Serverless
  - アップロード: S3（文書格納） → メタデータ生成
  - メタデータ抽出: メタデータ生成 → Bedrock基盤モデル
  - トリガー: メタデータ生成 → Lambda関数
```

棒グラフのPDFも値とプランの組と縦軸の目盛りがそろっています。

```markdown
- 左：監査ログ保持期間（日）
  - 縦軸：保持日数（対数スケール、10¹〜10³）
  - Free：30日
  - Standard：90日
  - Enterprise：730日
- 右：APIレート制限（req/sec）
  - 縦軸：リクエスト毎秒（対数スケール、10¹〜10³）
  - Free：10 req/sec
  - Standard：100 req/sec
  - Enterprise：1,000 req/sec
```

出力トークンはHaiku 4.5より多めですが、東京の`jp.`推論プロファイルの単価はHaiku 4.5の10分の1（入力$0.11・出力$0.55／100万トークン）です。4件の費用はHaiku 4.5の約$0.043に対してHaiku 5.5は約$0.0053でした。

もし今お使いのAWSアカウントでHaiku 5.5を使用できるならこちらは選択肢としてかなり有力なんじゃないでしょうか！
ぜひ読み込みたいドキュメントで試すことをお勧めします。

## サイズが大きいPDFを変換する場合

このスクリプトはPDFを1件ずつ送るのでConverseの文書入力は[1件4.5 MBまで](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Message.html)、Claudeへ渡すPDFは[1リクエストあたり100ページまで](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-api-restrictions.html)なので、どちらかを超えるPDFは先に分割してから入力ディレクトリへ配置します。

Claude Haiku 5.5を使う場合はもう1つ目安があります。1リクエストのプロンプトが10万トークンを超えると、そのリクエストの入力と出力の全体が5倍の単価になります（[Anthropicの料金ページ](https://platform.claude.com/docs/en/about-claude/pricing#long-context-pricing)）。本のサンプルPDFは1ページあたり約2,300〜2,600トークンだったので、40ページ前後で超える見込みで、Haiku 5.5で変換するなら40ページを目安に分割するとよいかもしれません。

出力にも上限があり、1件の出力をHaiku 4.5の上限と同じ64,000トークンまでにしています。本のサンプルPDFではHaiku 5.5の出力が1ページあたり平均約1,100トークンだったので、今回のサンプルと同程度の出力なら40ページでも約44,000トークンで収まる計算です。文字の多い資料で上限に届いた場合はスクリプトが途中までの結果を保存せずにエラーで止まるのでさらに小さく分割してください。

PDFはページ画像も入力トークンとして消費するため、費用は本文の文字数から見積もるより、スクリプトが記録した入力・出力トークン数で計算すると、図表の多い資料も含めて把握できます。

## おわりに

単発でこのようにスクリプト実行してもいいですし、自動的にドキュメントが追加されたらマークダウン変換するようなパイプラインの一部としても組み込むことができると思います。参考になったら嬉しいです！