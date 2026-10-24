# WeatherAgentの実装

都市名に対応する固定の天気データをツールで取得し、Strands Agentが回答するアプリです。起動とデプロイは、2階層上の`weatherDemo/`で行います。[プロジェクト全体の手順](../../README.md)から始めてください。

## 処理を追う順番

| ファイル | 読むところ |
|---|---|
| `main.py` | `get_current_weather`が天気を返し、`invoke`がエージェントの応答をストリームで返す |
| `model/load.py` | `BedrockModel`にClaude Sonnet 4.6のモデルIDを指定する |
| `memory/session.py` | Memory IDが設定されている場合に、セッションマネージャーを作る |
| `pyproject.toml`・`uv.lock` | アプリのPython依存関係 |

東京・大阪・札幌の天気は`main.py`の辞書に書いた架空のデータです。別の都市を加える場合や外部APIにつなぐ場合は、このツールを変更します。

## 入力と会話履歴

通常の質問は`prompt`に文字列で渡します。`_extract_prompt()`が入力形式を検査してからStrandsへ渡します。`messages`による履歴を受け取る経路では、`strip_trailing_tool_use()`が末尾のツール呼び出しを調整します。

## 環境変数

| 変数 | 用途 |
|---|---|
| `AWS_REGION` | AWS SDKとMemoryの接続先リージョン |
| `MEMORY_WEATHERAGENTMEMORY_ID` | 利用するMemory ID。未設定の場合はMemoryを使わない |

モデルIDを変更するときは`model/load.py`を編集します。この実装は`MODEL_ID`環境変数を読みません。

[起動・デプロイの手順に戻る](../../README.md)
