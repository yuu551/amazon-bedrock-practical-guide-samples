# コンテキストグラウンディングチェックのStandardティアを試す

ClassicとStandardのガードレールを一時的に作り、日本語の回答の点数を比べます。終わると、作ったガードレールは削除されます。

```bash
uv run --frozen python check_grounding.py
```

複数ターンの会話での評価は、次のスクリプトで試せます。

```bash
uv run --frozen python check_multiturn.py
```

詳しくは、オンライン補足の[コンテキストグラウンディングチェックにStandardティアが追加された](../../../supplements/chapter01/コンテキストグラウンディングチェックにStandardティアが追加された.md)を参照してください。
