# AuroraでKnowledge Basesの検索を検証する

第2章ではハイブリッド検索や検索設定の調整を試すために、Amazon OpenSearch Serverlessをベクトルストアに選びました。今回触れていないですがAmazon Aurora PostgreSQLでも、pgvectorという拡張機能を使ってKnowledge Basesのベクトルストアにできます。

第2章の文書を取り込み、S3 VectorsやOpenSearchとの検索結果の違いも簡単に比べてみました。

## サンプルが作る構成

文書は第2章と同じくS3に配置します。Knowledge Basesがチャンクに分割し、Titan Text Embeddings V2でベクトル化してAuroraに保存します。質問時もKnowledge BasesのRetrieveやRetrieveAndGenerateを使うため、アプリケーション側で検索用のSQLを書く必要はありません。

![S3の文書をKnowledge Basesでベクトル化し、RDS Data API経由でAuroraへ保存する構成](images/aurora-kb-architecture.png)

今回使用するサンプルのエントリーポイントは`sample-code/supplements/aurora-kb/aurora_kb.py`です。  
下記の順に構築を進めていきます。

1. `infrastructure.py`で定義したCloudFormationテンプレートから、Aurora・S3・Secrets Manager・Knowledge Bases用のIAMロールを作成する。
2. RDS Data APIでDBユーザーを作り、`schema.sql`を実行してテーブルとインデックスを用意する。
3. 作成したテーブルを保存先に指定してKnowledge Baseを作り、S3のデータソースを登録する。

この順番にしているのは、Knowledge Baseの作成時に接続先のテーブルと列を指定するためです。

DBは専用VPC内に置き、SQLの実行にはHTTPSで呼び出せるData APIを使います。

## テーブルを作ってKnowledge Basesへ接続する

### 1チャンクを1行に保存する

`schema.sql`ではpgvectorを有効にし、本文とベクトルを同じ行に保存するテーブルを作ります。

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS bedrock_integration;
CREATE TABLE IF NOT EXISTS bedrock_integration.bedrock_kb (
    id uuid PRIMARY KEY,
    embedding vector(1024),
    chunks text,
    metadata json,
    custom_metadata jsonb
);
```

本文は`chunks`、その意味を表す1024次元のベクトルは`embedding`に入ります。次元数はTitan Text Embeddings V2の出力と合わせておきましょう。出典などをKnowledge Basesが管理する`metadata`列と、文書に添付した部門・文書種別・適用日を保存する`custom_metadata`列に分けています。JSONBで保存するので、メタデータの項目ごとに列を増やす必要はありません。

検索用には、近いベクトルを探すHNSWインデックスと、本文の全文検索・メタデータの絞り込みに使うGINインデックスを作ります。

```sql
CREATE INDEX IF NOT EXISTS bedrock_kb_embedding_idx
    ON bedrock_integration.bedrock_kb USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS bedrock_kb_text_idx
    ON bedrock_integration.bedrock_kb USING gin (to_tsvector('simple', chunks));
CREATE INDEX IF NOT EXISTS bedrock_kb_metadata_idx
    ON bedrock_integration.bedrock_kb USING gin (custom_metadata);
```

ベクトル同士の近さにはコサイン距離を使います。テーブルの構成は[AWSのAurora向け設定手順](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-setup.html)に合わせています。

ただし、全文検索のインデックスに使う`simple`辞書は日本語を語に区切れないため、日本語の文書では全文検索がヒットしません。それでもハイブリッド検索には、結果の並び順を正しくする効果がありました。詳しくは後半で説明します。

### 列の用途と接続先を渡す

Knowledge Baseを作るときは保存先をRDSにし、クラスター・認証情報・テーブル名に加えて各列の用途を指定します。`setup()`が`create_knowledge_base()`へ渡す保存先設定は次のとおりです。

```python
storageConfiguration={
    "type": "RDS",
    "rdsConfiguration": {
        "resourceArn": self.state["ClusterArn"],
        "credentialsSecretArn": self.state["KbSecretArn"],
        "databaseName": DB,
        "tableName": TABLE,
        "fieldMapping": {
            "primaryKeyField": "id",
            "vectorField": "embedding",
            "textField": "chunks",
            "metadataField": "metadata",
            "customMetadataField": "custom_metadata",
        },
    },
}
```

データベース名には`knowledge`、テーブル名には先ほど作成した`bedrock_integration.bedrock_kb`を指定しています。この対応付けによって、Knowledge Basesがどの列へベクトルや本文を書き込むかが決まります。埋め込みモデルの設定でも1024次元を指定しています。

認証情報はSecrets Managerに保存し、初期設定を行う管理ユーザーと、専用テーブルを読み書きするKnowledge Bases用ユーザーを分けています。スクリプトがSQLを実行する`sql()`でも、次のように処理に応じてシークレットを切り替えます。

```python
return self.client("rds-data").execute_statement(
    resourceArn=self.state["ClusterArn"],
    secretArn=self.state["MasterSecretArn" if admin else "KbSecretArn"],
    database=DB,
    sql=statement,
    parameters=parameters or [],
    formatRecordsAs="JSON",
)
```

SQLの結果はJSONで受け取り、後述の件数確認にも使っています。

## 構築して第2章の文書を取り込む

スクリプトを通じて一式作成します。

```bash
cd sample-code/supplements/aurora-kb
uv sync --frozen
aws sts get-caller-identity
uv run python aurora_kb.py deploy --yes
uv run python aurora_kb.py ingest
uv run python aurora_kb.py inspect
```

既定ではバージニア北部にAurora PostgreSQL ServerlessのWriterを1台作ります。処理容量は0〜2 ACUで、1時間操作がないと自動で停止します。停止中は処理容量の料金がかからず、ストレージの料金だけになります。

`ingest()`は第2章のMarkdownと、文書に対応するメタデータファイルをS3の`documents/`以下へアップロードします。

その後、`start_ingestion_job()`でKnowledge Basesの同期を開始します。データソースには300トークン、オーバーラップ20%の分割を設定しています。分割・ベクトル化・DBへの書き込みはKnowledge Basesが行います。スクリプトは同期完了を待ち、取り込みに失敗した文書があればエラーにします。

`inspect()`はData API経由でSQLを実行し、たとえば保存された行数と次元数は次のSQLで調べます。

```sql
SELECT count(*) AS chunks,
       min(vector_dims(embedding)) AS min_dimensions,
       max(vector_dims(embedding)) AS max_dimensions
FROM bedrock_integration.bedrock_kb;
```

`inspect`を実行すると、次のような結果が表示されます（抜粋）。同じ内容は`.state/inspection.json`にも保存されます。

```json
{
  "versions": [{ "postgres": "PostgreSQL 16.14 on aarch64-unknown-linux-gnu, ...", "pgvector": "0.8.2" }],
  "counts": [{ "chunks": 126, "min_dimensions": 1024, "max_dimensions": 1024 }],
  "documents": [
    { "document_type": "faq", "chunks": 38 },
    { "document_type": "manual", "chunks": 60 },
    { "document_type": "policy", "chunks": 21 },
    { "document_type": "release_note", "chunks": 7 }
  ]
}
```

33文書が126チャンクになり、次元数はすべて1024でした。文書種別ごとのチャンク数も、SQLの集計でそのまま確かめられます。

## 検索方式と適用日のフィルタを変える

検索はAuroraへ直接SQLを送らず、`retrieve()`からKnowledge Basesへリクエストを送ります。

```python
def retrieve(self, question, mode="SEMANTIC", query_filter=None):
    cfg = {"numberOfResults": 5, "overrideSearchType": mode}
    if query_filter:
        cfg["filter"] = query_filter
    return self.client("bedrock-agent-runtime").retrieve(
        knowledgeBaseId=self.state["kb_id"],
        retrievalQuery={"text": question},
        retrievalConfiguration={"vectorSearchConfiguration": cfg},
    )["retrievalResults"]
```

呼び出す側は検索方式と絞り込み条件を渡します。戻り値にはチャンクの本文・出典・メタデータ・スコアが含まれます。

「2026年4月適用のポリシーでは、Enterpriseプランの監査ログは何日保存されますか」と質問してみます。旧版のドキュメントもあるため、監査ログのトピックと適用日を組み合わせて絞ります。

```python
filt = {
    "andAll": [
        {"equals": {"key": "topic_id", "value": "audit_log"}},
        {"equals": {"key": "effective_date", "value": 20260401}},
    ]
}
```

ここで使うキーは、取り込み時に文書へ添付し、DBでは独自メタデータの列に保存したものです。

```bash
uv run python aurora_kb.py verify
```

このコマンドはセマンティック検索とハイブリッド検索の両方を実行し、条件に合うメタデータと「730日」を含む本文が取得できることを検査します。続いてClaude Sonnet 4.6によるRetrieveAndGenerateで、730日という回答と根拠文書の出典を確認します。

実行すると、検索方式ごとに回答が表示されます。

```text
SEMANTIC 2026年4月適用のポリシーでは、Enterpriseプランの監査ログは730日（24ヶ月）保存されます。
HYBRID 2026年4月適用のポリシーでは、Enterpriseプランの監査ログは730日（24ヶ月）保存されます。これは前版の365日から延長されたものです。
```

どちらの方式も、回答の出典は`policies/監査ログ保存ポリシー2026年版.md`でした。`.state/verification.json`に残る検索結果は4件で、すべて適用日が2026年4月1日の監査ログの文書です。365日と書かれた旧版のポリシーは入っていません。

厳しいフィルタでは近似検索で得た候補が除外され、今回のように5件より少なくなる場合があります。サンプルではpgvector 0.8.0以降なら、候補が足りないときに探索を続ける反復スキャンもDB単位で有効にしています。

## 18問で取得文書と順位を比べる

第3章の`questions.jsonl`にある18問を使い、最大5チャンクから期待する文書を取得できたかを採点します。

```bash
uv run python aurora_kb.py evaluate
```

`evaluate()`は質問ごとに`retrieve()`を呼び出し、返されたS3の出典パスを期待文書と照合します。回答の文章を採点する処理は入っていません。表の指標は次の2つです。

- 取得できた質問の割合は、期待文書を1件以上取得できた質問の割合です。
- 文書の網羅率は、取得できた期待文書数を必要な期待文書数で割り、全問で平均した値です。同じ文書の複数チャンクは1文書として数えます。

Auroraのpgvectorは0.8.2で、1回ずつ検索しました。比較するS3 Vectors・OpenSearchの保存済みログも、33文書とメタデータ・18問の質問文が一致することを確かめ、同じ期待文書で採点しています。埋め込みはすべてTitan Text Embeddings V2の1024次元です。条件をそろえるため、どの保存先もセマンティック検索・フィルタなし・リランキングなしの結果を載せています。


| 保存先        | 取得できた質問の割合 | 文書の網羅率 |
| ---------- | ----------: | ------: |
| Aurora     | 0.944      | 0.741  |
| S3 Vectors | 0.944      | 0.741  |
| OpenSearch | 0.944      | 0.741  |


どの保存先でも18問中17問で期待文書を取得でき、網羅率も0.741でした。今回の文書と質問では、保存先を変えても取得できる期待文書は変わりません。

取得したドキュメント順位の違いも見ておきましょう。Auroraだけは、期待文書が上位に来ない質問が目立ち、その原因は並び順でした。AuroraのSQLログを見ると、Knowledge Basesは質問に近い5チャンクを正しく取り出していましたが、`score`にはコサイン距離が入っていて、小さいほど近いのに大きい順に並べて返していました。

SAML認証の設定項目を聞く質問001で、実際の順番を比べます。左はAuroraで全件の距離を計算し、近い順に並べた5チャンクです。右はKnowledge Basesのセマンティック検索とハイブリッド検索が返した順番です。


| 順位  | 全件の距離で近い順（距離）          | セマンティック検索の結果（score）    | ハイブリッド検索の結果    |
| ---: | ---------------------- | ---------------------- | -------------- |
| 1   | SAML認証設定ガイド（0.3174）    | リリースノート2026年4月（0.4878） | SAML認証設定ガイド    |
| 2   | SAML認証設定ガイド（0.3844）    | SAML認証設定ガイド（0.4496）    | リリースノート2026年4月 |
| 3   | SAML認証設定ガイド（0.4017）    |                        |                |
| 4   | SAML認証設定ガイド（0.4496）    |                        |                |
| 5   | リリースノート2026年4月（0.4878） |                        |                |


セマンティック検索は、近い5チャンクのうち最も遠いリリースノートを1位にしていました。設定ガイドの4チャンクは1件にまとめられ、そのscoreも4チャンクの中で最も遠い0.4496です。

ほかの質問でも1位は同じ傾向でした。


| 質問                  | セマンティック検索の1位（score）        | 全件の距離で最も近い文書（距離）      |
| ------------------- | -------------------------- | --------------------- |
| 001 SAML認証の設定項目     | リリースノート2026年4月（0.4878）     | SAML認証設定ガイド（0.3174）   |
| 003 APIキーを誤って公開した場合 | API認証・監査ログ管理ガイド（0.6335）    | APIキー誤公開時の対応（0.2174）  |
| 004 IPアドレス制限の有効化    | Enterpriseプラン機能ガイド（0.6091） | IPアドレス制限の設定範囲（0.3623） |
| 017 障害対応の3手順        | 障害発生時の顧客向け初回案内（0.5151）     | 障害一次対応手順（0.3799）      |


18問すべてで、セマンティック検索の結果はscoreの大きい順でした。

そこで、検索方式にハイブリッドを指定すると、ベクトル検索と全文検索の順位を合わせてから並べるので、この逆転が起きませんでした。18問の順位は、ほかの保存先のセマンティック検索と同じになりました。AuroraをKnowledge Basesで使うなら、並び順のためにハイブリッドを指定しておくと取得順がよくなる可能性があります。

## 日本語のキーワードはpg_bigmとSQLで探す

Knowledge Basesのハイブリッド検索は、全文検索にPostgreSQL標準の仕組みを使います。標準の辞書には日本語を語に区切るものがなく、Auroraでは辞書を追加することもできません。前の節のとおり、18問の質問はどれも全文検索でヒットしませんでした。

日本語のキーワード一致が必要なら、2文字ずつに区切って索引を作る`pg_bigm`拡張をAuroraに入れ、SQLで検索します。`pg_bigm`はKnowledge Basesの検索からは使われませんが、Knowledge Basesが作ったテーブルに索引を足すだけで使えます。[PostgreSQLの辞書とpg_bigmの違い](https://aws.amazon.com/blogs/database/migrate-multilingual-full-text-search-from-sql-server-to-postgresql/)

```sql
CREATE EXTENSION IF NOT EXISTS pg_bigm;
CREATE INDEX IF NOT EXISTS bedrock_kb_chunks_bigm_idx
    ON bedrock_integration.bedrock_kb USING gin (chunks gin_bigm_ops);

SELECT metadata::json->>'sourceUrl' AS source, chunks
FROM bedrock_integration.bedrock_kb
WHERE chunks LIKE likequery('エスカレーション');
```

拡張と索引は管理者のDBユーザーで作ります。`likequery()`はキーワードの前後に`%`を付け、記号をエスケープして`LIKE`の検索パターンにする関数です。第2章の126チャンクで、Knowledge Basesと同じ全文検索とヒットした件数を比べました。


| キーワード     | Knowledge Basesと同じ全文検索 | `pg_bigm` |
| --------- | ----------------------: | ---------: |
| 監査ログ      | 11                     | 30        |
| 保持期間      | 2                      | 12        |
| エスカレーション  | 0                      | 14        |
| サービスクレジット | 0                      | 18        |
| IPアドレス制限  | 3                      | 7         |
| 一次対応      | 0                      | 9         |
| 請求書払い     | 2                      | 4         |


`pg_bigm`では、キーワードを含むチャンクがすべてヒットしました。全文検索でヒットしたのは、キーワードの前後が記号や空白で区切られていたチャンクだけです。

ベクトル検索と組み合わせる場合も、同じテーブルにSQLを1本書けば済みます。次の例は「保持期間」を含むチャンクに絞り、質問のベクトルに近い順で5件を返します。質問のベクトルは、取り込みと同じTitan Text Embeddings V2でアプリケーション側が作ります。

```sql
SELECT metadata::json->>'sourceUrl' AS source,
       round((embedding <=> CAST(:question_vector AS vector))::numeric, 4) AS distance
FROM bedrock_integration.bedrock_kb
WHERE chunks LIKE likequery('保持期間')
ORDER BY embedding <=> CAST(:question_vector AS vector)
LIMIT 5;
```

「Security部門が管理する監査ログの保持期間とエクスポート方法を教えてください」（質問013）のベクトルで実行すると、次の5行が返りました。`source`はS3のパスのうち`documents/`以降だけを載せています。


| source                       | distance |
| ---------------------------- | --------: |
| faq/監査ログの保持期間.md             | 0.2828   |
| product/API認証・監査ログ管理ガイド.md   | 0.3245   |
| policies/監査ログ保存ポリシー2025年版.md | 0.3372   |
| product/API認証・監査ログ管理ガイド.md   | 0.3430   |
| policies/監査ログ保存ポリシー2026年版.md | 0.3510   |


どれも「保持期間」を含むチャンクで、距離の小さい順に並んでいます。

Knowledge BasesのRetrieveでは書けない検索なので、取り込みはKnowledge Basesに任せ、検索だけアプリケーションから行う形になります。

## Auroraを選択する場合を考えてみる

小規模なFAQで必要な文書が取れているなら、まず検索用のリソースを常時確保する固定費がないS3 VectorsやManaged KBから始めたいと考えています。ハイブリッド検索や、SQLで取り込み後のデータを調べる必要が出てきたら、Auroraも候補に加えます。

AuroraではDBの更新・インデックス・容量を管理します。既存の業務DBへ同居させる場合は、文書の同期が業務クエリと処理容量を取り合う点にも注意が必要です。業務テーブルとベクトル検索結果を結合できる点にも魅力がありますが、Knowledge BasesのRetrieveへJOIN文を渡すことはできず、その用途ではアプリケーションからSQLを実行する処理を自前で用意する必要があります。

OpenSearchとの比較でいうと、今回の33文書・18問では取得できる文書に差はありませんでした。Auroraは並び順に気をつければ、順位もS3 Vectors・OpenSearchと同じような結果に今回はなっています。（あくまで今回の結果なので、実際に使用される文書で比較して判断ください）

そのため、精度、Knowledge Basesとの連携や運用のしやすさを総合して選ぶのが良いと思います。連携の面では、保存先によって標準でできることが違います。


| 観点                | Aurora                        | OpenSearch Serverless                | S3 Vectors              | Managed KB                             |
| ----------------- | ----------------------------- | ------------------------------------ | ----------------------- | -------------------------------------- |
| 保存先の準備            | クイック作成できる                     | クイック作成できる（アナライザーは選べない）               | クイック作成できる               | 不要（AWSが管理）                             |
| ハイブリッド検索          | できる。日本語の全文検索はヒットしない           | できる。kuromojiのインデックスを自分で作れば語の単位でヒットする | できない（セマンティック検索のみ）       | 常にハイブリッド。セマンティック検索だけにはできない             |
| リランキング            | モデルを指定すれば使える                  | モデルを指定すれば使える                         | モデルを指定すれば使える            | 既定で有効                                  |
| メタデータでの絞り込み       | できる。独自メタデータの列が必要              | できる                                  | できる。1ベクトルあたり1 KB・35キーまで | できる。`startsWith`・`stringContains`は使えない |
| 取り込んだチャンクをSQLで調べる | できる                           | できない                                 | できない                    | できない                                   |
| 日本語のキーワード検索       | `pg_bigm`を入れてアプリケーションからSQLで行う | ハイブリッド検索で行える                         | できない                    | ハイブリッド検索に含まれる（区切り方は確かめていない）            |
| 使わない間の処理容量の料金     | 自動停止で0                        | Classicは2 OCUが残る                     | なし                      | なし                                     |


また費用は、使わない間にどれだけかかるかで大きく変わります。`us-east-1`の単価で比べると次のとおりです。


| 保存先                                                  | 使わない間                                            | 動いている間                               |
| ---------------------------------------------------- | ------------------------------------------------ | ------------------------------------ |
| Aurora（このサンプルの設定）                                    | 1時間操作がないと停止し、処理容量の料金は0。ストレージは1 GB-monthあたり0.10ドル | 1 ACU時間あたり0.12ドル。上限の2 ACUなら1時間0.24ドル |
| OpenSearch Serverless Classic（第2章の設定）                | 2 OCUが残り、1時間0.48ドル（24時間で11.52ドル）                 | 1 OCU時間あたり0.24ドル。負荷に応じてOCUが増える       |
| OpenSearch Serverless NextGen（Knowledge Basesでは使えない） | 最小OCUはなく、10分間リクエストがないと0 OCUになる。保存料金は別            | 1 OCU時間あたり0.24ドル（Classicと同じ単価）       |
| S3 Vectors                                           | 処理容量の料金はなし。保存は1 GB-monthあたり0.06ドル                | 書き込み・検索のリクエストと処理量に応じた料金              |
| Managed KB                                           | 処理容量の料金はなし。元の文書の保存量に応じた料金                        | 検索の呼び出し回数に応じた料金（トークン数ではない）           |


すでにPostgreSQLを運用していて、取り込んだチャンクをSQLで検索、既存テーブルと結合したいならAuroraが合うと思いました。繰り返しになりますが、ただゼロスタートでAuroraを！とはならず、その場合はまずS3 VectorsやManaged KBで検証を進めた方が良い印象です。

AuroraやOpenSearch Serverlessはコストが安いわけではないので、コストと機能を天秤において採用するベクトルデータベースを検討しましょう。

## 検証リソースを削除する

```bash
uv run python aurora_kb.py cleanup --yes
```

Knowledge Base・専用S3バケット内のオブジェクト・CloudFormationのリソースを削除します。DBやシークレットも削除します。

[オンライン補足の一覧へ戻る](../README.md)