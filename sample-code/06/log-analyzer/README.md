# Bedrock モデル呼び出しログ分析ツール

CloudWatch Logs InsightsでBedrockのモデル呼び出しログを集計する、第6章のツールです。モデル別・推論プロファイル別・時間帯別に、トークン数や応答時間を確認できます。

## 前提条件

- Bedrock のモデル呼び出しログが有効化されていること（CloudWatch Logs への出力）
- `logs:StartQuery`, `logs:GetQueryResults` の IAM 権限があること
- Python 3.12 以上

## セットアップ

```bash
cd sample-code/06/log-analyzer
uv sync
```

## 使い方

日時は、調べたいログが存在する期間に置き換えてください。既定のロググループは`/aws/bedrock/modelinvocations`です。異なる場合は`--log-group`を指定します。`--include-kb`を使う場合は、Knowledge Bases側のロググループを`--kb-log-group`で指定できます。

```bash
# モデル別集計
uv run python analyze_logs.py \
    --start-time 2026-04-01T00:00:00 \
    --end-time 2026-04-11T00:00:00 \
    --group-by model

# Inference Profile 別集計（アプリケーション単位のコスト把握）
uv run python analyze_logs.py \
    --start-time 2026-04-01T00:00:00 \
    --end-time 2026-04-11T00:00:00 \
    --group-by profile

# 時間帯別集計（レイテンシ傾向の確認）
uv run python analyze_logs.py \
    --start-time 2026-04-01T00:00:00 \
    --end-time 2026-04-11T00:00:00 \
    --group-by hour

# エラー集計と Knowledge Bases 取り込み状況も含める
uv run python analyze_logs.py \
    --start-time 2026-04-01T00:00:00 \
    --end-time 2026-04-11T00:00:00 \
    --group-by model \
    --include-errors \
    --include-kb
```

## 補足

- コスト推定はオンデマンド料金の参考値です。実際の請求額は AWS Cost Explorer で確認してください
- `queries.py` のクエリは CloudWatch コンソールに直接貼り付けても使えます

[第6章の構成一覧に戻る](../README.md)
