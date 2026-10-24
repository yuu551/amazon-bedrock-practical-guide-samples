# OpenSearch Serverless NextGenをKnowledge Basesで使えるか試す

Amazon OpenSearch Serverlessには、使わない間の費用を抑えられるNextGenがあります。第2章ではClassicを使いましたが、NextGenもKnowledge Basesの保存先にできないか試してみましたが、結果はKnowledge Baseの作成でエラーになりました。

そこでOpenSearchのAPIから直接検索し、接続の問題なのか検索自体が動かないのかを切り分けました。

## Knowledge Baseの作成で失敗

NextGenでは最小容量を0に設定したコレクショングループなら、10分間リクエストがないと計算資源を0 OCUまで減らせます。保存料金は残り、再開に10〜30秒ほどかかることもありますが、検証環境を置いておく費用を抑えられるのは魅力です。

一方Classicはこの縮退の対象外です。

[AWSの仕様説明](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-scale-to-zero.html)

切り替えたいと思い、バージニア北部でNextGenのベクトル検索用コレクションを作り、Titan Text Embeddings V2の1024次元ベクトルを保存するインデックスを用意しました。Knowledge Basesへ接続した結果は次のとおりです。

| インデックスの指定 | NextGenのインデックス作成 | Knowledge Baseの作成 |
|---|---|---|
| ベクトルの型と次元数を指定 | 成功 | FAISSの要件を満たさず失敗 |
| `method.engine: faiss`も指定 | `engine`を受け付けず失敗 | インデックスを作成できず、接続へ進めない |

残念ながら失敗に終わりました。Knowledge Basesが求めるFAISSを指定すると、今度はNextGenが受け付けません。

## 質問をベクトル化して直接検索する

NextGenの検索が使えるかどうかも未知数なので、Knowledge Basesを使わずに直接検索することにしました。

そこでインデックスに2件のテスト文書を登録し、Knowledge Basesを介さずに検索しました。質問は「Enterpriseプランの監査ログは何日保存されますか」です。

![質問をTitan Embeddings V2でベクトル化し、NextGenを直接検索して文書を取得する構成](images/nextgen-direct-search.png)

この処理をまとめたのが、`sample-code/02/nextgen_direct_retrieve.py`で、文書を登録済みのインデックスに対して、質問のベクトル化・OpenSearchへの検索リクエスト・結果の整形を行います。

### 質問にも文書と同じ埋め込みモデルを使う

質問文はそのままではベクトル検索に使えないので、`embed_question()`でTitan Text Embeddings V2を呼び出してベクトルにします。リクエストの中心は次の部分です。

```python
response = client.invoke_model(
    modelId=model_id,
    contentType="application/json",
    accept="application/json",
    body=json.dumps({
        "inputText": question,
        "dimensions": dimensions,
        "normalize": True,
    }),
)
payload = json.loads(response["body"].read())
embedding = payload.get("embedding")
```

モデルは本書と同じ`amazon.titan-embed-text-v2:0`、次元数に1024を指定し、戻り値の数値配列を検索に使います。関数では配列の長さも確認しています。文書と質問で埋め込みモデルが違うと、次元数をそろえても意味の近さを比べられないので、文書を登録したときと同じ設定にします。

### ベクトルをOpenSearchの検索条件に入れる

セマンティック検索では、質問のベクトルと文書のベクトルが入っているフィールドを比べます。`semantic_query()`が組み立てるリクエスト本文は次の形です。

```python
body = {
    "size": top_k,
    "_source": [text_field, metadata_field],
    "query": {
        "knn": {
            vector_field: {
                "vector": embedding,
                "k": candidate_count,
            }
        }
    },
}
response = opensearch.search(index=args.index, body=body)
```

既定では候補数を50、返す件数を4にしています。各フィールド名はデフォルトのインデックスの定義に合わせて指定します。

OpenSearchクライアントはAWS認証情報でリクエストに署名し、HTTPSで送信します。`create_opensearch_client()`では、`AWSV4SignerAuth(credentials, region, "aoss")`を指定しています。Bedrockでのモデル呼び出しとOpenSearch Serverlessでの検索という2段階の処理なので、両方の権限が必要です。

## ハイブリッド検索では2つのスコアを統合する

ハイブリッド検索では同じ質問でキーワード検索とベクトル検索の両方を実行します。`hybrid_query()`では先ほどの検索条件を次の形に変えます。

```python
query = {
    "hybrid": {
        "queries": [
            {"match": {text_field: {"query": question}}},
            {"knn": {vector_field: {
                "vector": embedding,
                "k": candidate_count,
            }}},
        ]
    }
}
```

2つの検索はスコアの尺度が違うので、検索パイプラインで正規化してから統合します。`search_pipeline_body()`で作る設定の要点は次の部分です。

```python
processor = {
    "normalization-processor": {
        "normalization": {"technique": "min_max"},
        "combination": {
            "technique": "arithmetic_mean",
            "parameters": {
                "weights": [keyword_weight, 1 - keyword_weight]
            },
        },
    },
}
```

最小値と最大値でスコアの範囲をそろえたあと、重み付き平均を取ります。重みは検索条件と同じ順に並び、既定ではキーワードが0.3、ベクトルが0.7です。検索時に`params={"search_pipeline": args.search_pipeline}`を渡すと、この処理が適用されます。

検索結果は、`normalize_hits()`で文書ID・スコア・本文・メタデータに整形して表示します。この関数は表示の形をそろえるだけで、スコアの正規化はOpenSearch側の検索パイプラインが行います。

## 直接検索で得られた結果

| 検索方式 | 1位の文書 | 返されたスコア |
|---|---|---:|
| セマンティック検索 | `audit-log` | 0.767133 |
| ハイブリッド検索 | `audit-log` | 1.0 |

どちらも監査ログの文書を1位に返しました。ハイブリッド検索のスコアは正規化を経た値のため、1.0と0.767133の大小で精度は比べられませんが、どちらの手法も問題なく検索ができることは確認できました。

## 登録済みインデックスで試す

接続先とインデックス名を自分の環境に置き換えて、第2章のディレクトリで実行することもできます。

```bash
cd sample-code/02
uv sync --frozen
export AOSS_ENDPOINT=https://your-collection-endpoint
export AOSS_INDEX_NAME=your-index-name
uv run nextgen_direct_retrieve.py \
  --endpoint "$AOSS_ENDPOINT" \
  --index "$AOSS_INDEX_NAME" \
  --region us-east-1 \
  --question 'Enterpriseプランの監査ログは何日保存されますか'
```

埋め込みモデルとフィールド名は、必要に応じて`--embedding-model`・`--dimensions`・`--vector-field`・`--text-field`・`--metadata-field`で変えられます。実行する人にはTitanの呼び出し権限と、対象コレクションへのIAM権限・データアクセスポリシーが必要です。通信元もネットワークポリシーで許可します。

ハイブリッド検索をする場合には初回に検索パイプラインを作成します。

```bash
uv run nextgen_direct_retrieve.py \
  --endpoint "$AOSS_ENDPOINT" \
  --index "$AOSS_INDEX_NAME" \
  --region us-east-1 \
  --search-type hybrid \
  --search-pipeline book-nextgen-hybrid-test \
  --create-search-pipeline \
  --question 'Enterpriseプランの監査ログは何日保存されますか'
```

`--create-search-pipeline`を付けると、同じ名前のパイプラインがあれば上書きされます。まずは検証用の名前で作成します。2回目以降はこのオプションを外し、作ったパイプライン名を指定して実行してください。

## アプリケーションへ組み込むなら

直接検索では質問のベクトル化と検索条件の組み立てを自分で実装する必要があり、回答まで作るなら取得した本文をモデルへ渡す処理も要ります。文書の取り込み・メタデータによるアクセス制御・回答への引用の付与といった、Knowledge Basesに任せていた部分もアプリケーション側で用意することになります。

使わない時間の費用を抑えられるNextGenには期待していますが、今回の結果では本書のKnowledge Basesの保存先をそのまま置き換えられませんでした。どうしても使いたい場合は自前での実装コストと勘案してトライしてみましょう！
