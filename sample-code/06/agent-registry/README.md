# エージェントとスキルをAWS Agent Registryで管理する

レジストリへ`AGENT`、`MCP`、`SKILL`を登録し、エージェントが実行時に承認済みのスキルを読み込む流れを1段ずつ試します。スキルは`registry_skill_loader.py`でRegistryから検索し、Strands Agentsの`AgentSkills`に渡します。

## 実行する

[共通のAWS認証](../../README.md#認証情報)を設定してから実行します。使用するboto3は`1.43.83`です。

```bash
cd sample-code/06/agent-registry
uv sync --frozen

uv run python register_and_search_registry.py setup     # レジストリとレコードを下書きで作る
uv run python register_and_search_registry.py ask       # 承認前なので、スキルなしで回答する
uv run python register_and_search_registry.py approve   # 3件を承認する
uv run python register_and_search_registry.py ask       # スキル1.0.0を読み込んで回答する
uv run python register_and_search_registry.py revise    # スキル1.1.0を下書きで作る
uv run python register_and_search_registry.py ask       # 審査中なので、1.0.0のまま回答する
uv run python register_and_search_registry.py approve   # 1.1.0を承認する
uv run python register_and_search_registry.py ask       # 1.1.0を読み込んで回答する
uv run python register_and_search_registry.py cleanup   # レコードとレジストリを削除する
```

作ったレジストリのIDは`.registry_state.json`に保存されます。途中でコンソールを開けば、各段階のレコードの状態を確認できます。`ask`の後ろに質問文を渡すと、別の質問でも試せます。

上の流れを通しで実行して最後に削除するときは、`demo --yes`を使います。

```bash
uv run python register_and_search_registry.py demo --yes
```

`update_registry_record()`の`descriptors`は新規作成時と形が異なります。変更するディスクリプターと、その中で変更する各フィールドを
`{"optionalValue": ...}`で包んで渡します。

## ローカルのテストと後片付け

AWSを使わない単体テストは次のコマンドで実行できます。

```bash
uv run python -m unittest -v
```

サンプル中のA2A URLはAgent Cardの登録形式を示すダミーです。レジストリは接続先を管理しますが、A2Aサービスそのものはデプロイしません。`cleanup`を実行せずに終えた場合や`demo`を強制終了した場合は、`book-agent-registry-`から始まるレジストリが残っていないか確認してください。

[第6章の構成一覧に戻る](../README.md)
