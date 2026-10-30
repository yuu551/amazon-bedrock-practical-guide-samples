# WeatherAgentを構築するCDKプロジェクト

AgentCore CLIがデプロイ時に使うプロジェクトです。`@aws/agentcore-cdk`のコンストラクトで、`agentcore.json`に定義したリソースを作ります。エージェントを動かすだけなら、このディレクトリを直接操作する必要はありません。[WeatherAgentの手順](../../README.md)を使ってください。

## 構成を確認する

| ファイル | 内容 |
|---|---|
| `bin/cdk.ts` | `agentcore/`の設定を読み、デプロイ先ごとにスタックを作る |
| `lib/cdk-stack.ts` | `AgentCoreApplication`を使うスタック定義 |
| `test/cdk.test.ts` | テンプレート生成のテスト |
| `package.json` | CDKとTypeScriptの依存関係、ビルド・テストコマンド |

## CDKコードを変更したとき

リポジトリのルートから次を実行します。ビルドとテストはAWSリソースを作成しません。

```bash
cd sample-code/01/weatherDemo/agentcore/cdk
pnpm install --frozen-lockfile
pnpm run build
pnpm test
```

AWSへ反映するときは、`weatherDemo/`へ戻り、デプロイ先設定を確認してからCLIを実行します。

```bash
cd ../..
agentcore validate
agentcore deploy
agentcore status
```
