# サンプルコードの依存関係に関する既知事項

サンプルコードの依存関係はDependabotとCIで監視します。修正版が存在する脆弱性はlockfileを更新し、推移依存で解決できない場合だけ`pnpm.overrides`で修正版を固定します。

依存更新の検証スクリプトは、対象となる旧バージョンがlockfileに残っていないことも直接確認します。`pnpm audit`の監査サーバーが応答しない場合はその障害だけを許容し、継続的な検知はDependabotで補います。

## Amplify UIのoverride

第2・3・4・6章では、Amplifyの開発ツールとUIが参照する次の推移依存を修正版へ固定しています。

- `brace-expansion 1.1.21`・`2.1.7`・`5.0.12`（`^1`・`^2`・`^5`の依存をそれぞれ同じメジャーの修正版へ固定）
- `fast-uri 3.1.8`
- `handlebars 4.7.9`
- `immutable 4.3.9`
- `lodash 4.18.1`
- `mysql2 3.23.1`（Amplifyのスキーマ生成ツールが`~3.9.7`で固定しているため）
- `nanoid 3.3.18`
- `dompurify 3.4.16`
- `mermaid 11.16.1`
- `postcss 8.5.23`

第3章では、ADOTが完全一致で固定しているOpenTelemetryの推移依存も修正版へ固定しています。

- `@opentelemetry/propagator-jaeger 2.11.0`
- `@opentelemetry/instrumentation-cassandra-driver 0.66.0`・`instrumentation-knex 0.65.0`・`instrumentation-mongoose 0.67.0`・`instrumentation-mysql 0.67.0`・`instrumentation-mysql2 0.67.0`・`instrumentation-oracledb 0.46.0`・`instrumentation-pg 0.73.0`・`instrumentation-tedious 0.40.0`

サンプルのLambdaは`aws-sdk`の計装だけを有効にするため、これらの計装は読み込まれても対象のライブラリには適用されません。

overrideを削除する場合は、上流パッケージの依存先が同じ修正版以上になったことをlockfileで確認し、ビルド、Amplifyバックエンドの型チェック、UI E2Eを実行します。

## 修正版待ちの依存関係

2026年10月8日時点で、次の依存関係には互換性のある修正版がありません。GitHub上のAlertは閉じず、上流のリリース後に更新します。

`brace-expansion`の1系・2系にも修正版が公開されたため、CVE-2026-14257の除外はやめて上記のoverrideで修正版へ固定しました。

### braces

第1章のAgentCore CDKと第2・3・4・6章のAmplify UIは、jestやAmplify CLIの推移依存として`braces 3.0.3`を含みます。CVE-2026-93687の修正版はまだ公開されていません。

この依存はローカルのテスト・生成・デプロイツールでのみ使われ、サンプルの実行時コードには含まれません。サンプルは信頼できない文字列をglobパターンとして渡しません。

### @graphql-tools/utils

Amplify CLIのコード生成ツールは`@graphql-tools/utils`の6・7・9・11系を使います。CVE-2026-104852の修正は12.0.1以降だけで提供されており、Amplifyの最新版も旧メジャーを指定しています。メジャーをまたぐoverrideはAPIを壊すおそれがあるため使用しません。

この依存はローカルのコード生成でのみ使われ、サンプルが生成対象として渡すのは本書のスキーマだけです。

CIの`pnpm audit`では上記2件のCVEだけを明示的に除外し、それ以外のHigh/Criticalは失敗させます。

### Moderate・Lowのまま残す依存関係

- `csv-parse 5.6.0`（第2・3・4・6章）は修正が7.0.2以降のメジャーだけで提供されています。Amplifyのスキーマ生成ツールが`^5.5.2`を指定しているため更新しません。
- `katex 0.16.47`（第2・3・4・6章と補足のManaged KBアプリ）は修正が0.18.2以降だけで提供されています。mermaidが`^0.16.45`を指定しているため更新しません。
- `sprintf-js 1.0.3`（第1章のAgentCore CDK）には修正版がありません。jestの推移依存としてテスト時にだけ使われます。

### ragas・diskcache

第5章はRAG評価のため`ragas 0.4.3`を直接使用し、推移依存として`diskcache 5.6.3`を含みます。両方ともPyPI上の最新版ですが、Dependabot Alert #55と#56には修正版が登録されていません。

本書のコードはRAGASのMulti-Modal Faithfulness Collectionsを使用せず、第三者が書き込めるキャッシュをDiskCacheから読み込みません。修正版が公開されるまでは、評価コードを信頼できるローカル環境でのみ実行します。
