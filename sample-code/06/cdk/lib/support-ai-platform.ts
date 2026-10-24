import { Construct } from "constructs";
import * as cdk from "aws-cdk-lib";
import * as bedrock from "aws-cdk-lib/aws-bedrock";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import * as aoss from "aws-cdk-lib/aws-opensearchserverless";
import * as s3vectors from "aws-cdk-lib/aws-s3vectors";
import * as cr from "aws-cdk-lib/custom-resources";
import {
  Guardrail,
  ContentFilterType,
  ContentFilterStrength,
  GeneralPIIType,
  FinancePIIType,
  GuardrailAction,
  TierConfig,
} from "@aws-cdk/aws-bedrock-alpha";

// ================================================================
// SupportAiPlatform Props
// ================================================================
export interface SupportAiPlatformProps {
  /** 環境名 — dev / stg / prod */
  envName: "dev" | "stg" | "prod";
  /** コピー元モデル（Cross-Region Inference Profile など）の ARN */
  modelSourceArn: string;
  /**
   * Guardrail に description として埋め込むタグ。値を変えると CFn UPDATE 時に
   * 新しい `CfnGuardrailVersion` が払い出される（番号付きバージョンは
   * description に差分があるときだけ採番される CFn 仕様）。
   * 省略時はスタック名を使い、明示的に固定したいリリースでは "v3" などを渡す。
   */
  guardrailReleaseTag?: string;
  /**
   * Guardrail の Standard tier が利用するガードレールプロファイル ID
   * （既定: "us.guardrail.v1:0"）。Standard tier はクロスリージョン
   * Guardrail 推論が前提のため、デプロイ先リージョンの地理境界に合わせて
   * 指定する。東京リージョンで動かす場合は "apac.guardrail.v1:0" を渡す。
   */
  guardrailProfileId?: string;
  /**
   * Knowledge Base の埋め込みモデル ARN（既定: Titan Text Embeddings V2、1024次元）。
   * 別リージョンで動かす際は、そのリージョンで提供されている埋め込みモデルへ
   * 差し替えること。
   */
  embeddingModelArn?: string;
  /**
   * Knowledge Base のベクトルストア選択。
   *   - "S3_VECTORS"          : S3 Vectors（デフォルト、サンドボックス向き・低コスト）
   *   - "OPENSEARCH_SERVERLESS": Amazon OpenSearch Serverless（より高機能・本番向き）
   */
  vectorStore?: "S3_VECTORS" | "OPENSEARCH_SERVERLESS";
  /** 既存環境を更新するときに保持するS3 Vectorsバケット名。 */
  vectorBucketName?: string;
  /**
   * Knowledge Basesのリランキング用権限をサービスロールへ追加する。
   * 既定はfalse。trueの場合、bedrock:RerankはAPI仕様上Resource "*"が必要だが、
   * InvokeModelはCohere Rerank 3.5のモデルARNに限定する。
   */
  enableReranking?: boolean;
  /**
   * デプロイ時にデータソースバケットへ取り込むサンプルデータの絶対パス。
   * 指定すると `BucketDeployment` で同期し、完了後に `StartIngestionJob`
   * を自動で発火する。読者は `pnpm exec ampx sandbox --identifier ch06-private`
   * 一発で「取り込み済み Knowledge Base」を試せる。
   */
  docsSourceDir?: string;
  /**
   * OpenSearch Serverless のデータアクセスポリシーへ追加する IAM プリンシパルの ARN。
   * `vectorStore: "OPENSEARCH_SERVERLESS"` の場合、CFn の `CfnIndex` リソースは
   * OSS データプレーンの `CreateIndex` を直接叩くため、CDK の CloudFormation 実行
   * ロール（既定 `cdk-hnb659fds-cfn-exec-role-...`）を含めておかないと
   * `Access denied for operation 'CreateIndex'.` で失敗する。
   * 既定では CDK 標準の qualifier `hnb659fds` 用 ARN を投入する。
   */
  ossDataAccessPrincipals?: string[];
  /**
   * OpenSearch Serverless のネットワークポリシーをインターネット公開にするか。
   * 既定は `false`（VPC エンドポイント経由のみ）。サンプル動作確認のため
   * 一時的に開放したいときだけ `true` を指定する。
   * 本番では必ず `false` に戻し、`ossSourceVpcEndpoints` で許可済み VPC エンド
   * ポイントだけを通すこと。
   */
  allowOssPublicAccess?: boolean;
  /**
   * OpenSearch Serverless ネットワークポリシーで許可する VPC エンドポイント ID。
   * `allowOssPublicAccess: false`（既定）かつ非空のときに `SourceVPCEs` として
   * ネットワークポリシーへ反映される。
   */
  ossSourceVpcEndpoints?: string[];
}

// ================================================================
// SupportAiPlatform — 問い合わせ対応 AI の本番化リソースをまとめる Construct
// ================================================================
//
// 本章（第6章）の IaC 節で示した通り、Guardrail / Application Inference
// Profile / Knowledge Base 一式を 1 つの Construct へ束ね、envName の切り替え
// だけで dev / stg / prod を再現できるようにしています。
//
// AgentCore Runtime は第4章の `createAgentCoreRuntime` 側で構築する想定で、
// 本 Construct が公開する `guardrailId` / `inferenceProfileArn` /
// `knowledgeBaseId` を Runtime の環境変数経由で参照するパターンを取ります。
//
export class SupportAiPlatform extends Construct {
  /** アプリケーション推論プロファイル の ARN */
  readonly inferenceProfileArn: string;
  /** Guardrail ID（呼び出し側の Converse で `guardrailIdentifier` に指定） */
  readonly guardrailId: string;
  /** 利用側アプリへ受け渡す Guardrail バージョン番号 */
  readonly guardrailVersion: string;
  /** Knowledge Base ID — Runtime からの Retrieve 呼び出しで利用 */
  readonly knowledgeBaseId: string;
  /** Knowledge Base のデータソース ID（Ingestion Job 起動時に必要） */
  readonly dataSourceId: string;
  /** データソースの S3 バケット名 */
  readonly docsBucketName: string;
  /** データソースの S3 バケット ARN */
  readonly docsBucketArn: string;
  /** 採用したベクトルストア種別 */
  readonly vectorStoreType: "S3_VECTORS" | "OPENSEARCH_SERVERLESS";

  constructor(scope: Construct, id: string, props: SupportAiPlatformProps) {
    super(scope, id);

    const stack = cdk.Stack.of(this);
    const region = stack.region;
    const account = stack.account;
    const envName = props.envName;
    const vectorStore = props.vectorStore ?? "S3_VECTORS";
    this.vectorStoreType = vectorStore;

    // --------------------------------------------------------------
    // 1. Guardrail（L1 — CfnGuardrail + CfnGuardrailVersion）
    // --------------------------------------------------------------
    // Bedrock Guardrail API は最低 1 ポリシーを必須にする。
    // L1 では最低限 PROMPT_ATTACK の入力フィルタだけ入れた形で
    // CREATE できる状態にしておき、実運用で必要なポリシーは
    // 本章 Guardrails 節（L2 Guardrail）の例を参照して追加する。
    //
    // 日本語の検出精度を確保するため tier は Standard を指定する
    // （Classic は英語・フランス語・スペイン語中心）。Standard tier は
    // クロスリージョン Guardrail 推論が前提のため、ガードレール
    // プロファイルの指定が必須になる。
    const guardrailProfileArn = `arn:aws:bedrock:${region}:${account}:guardrail-profile/${
      props.guardrailProfileId ?? "us.guardrail.v1:0"
    }`;
    const guardrail = new bedrock.CfnGuardrail(this, "Guardrail", {
      name: `support-${envName}-guardrail`,
      blockedInputMessaging: "入力内容を見直してから再試行してください。",
      blockedOutputsMessaging: "安全性の確認ができなかったため回答を控えます。",
      crossRegionConfig: { guardrailProfileArn },
      contentPolicyConfig: {
        contentFiltersTierConfig: { tierName: "STANDARD" },
        filtersConfig: [
          {
            type: "PROMPT_ATTACK",
            inputStrength: "HIGH",
            outputStrength: "NONE",
          },
        ],
      },
    });

    // CfnGuardrailVersion は description に差分があるときだけ新しい版番号を採番する。
    // `guardrailReleaseTag` を変えれば次回 UPDATE で新版が払い出されるので、
    // 本番アプリには `attrVersion`（"1", "2", ...）を渡して DRAFT を踏まないようにする。
    const releaseTag = props.guardrailReleaseTag ?? stack.stackName;
    const guardrailVersion = new bedrock.CfnGuardrailVersion(this, "GuardrailVersion", {
      guardrailIdentifier: guardrail.attrGuardrailId,
      description: `${envName} release (${releaseTag})`,
    });
    guardrailVersion.addDependency(guardrail);

    this.guardrailId = guardrail.attrGuardrailId;
    this.guardrailVersion = guardrailVersion.attrVersion;

    // --------------------------------------------------------------
    // 2. Guardrail（L2 — @aws-cdk/aws-bedrock-alpha）
    // --------------------------------------------------------------
    // L2 を使うと、禁止トピック・コンテンツフィルター・PII保護を
    // メソッドチェーンで設定できます。L1 と L2 のどちらを使うかは
    // alpha パッケージの採用可否で判断してください。
    //
    // ここでは L2 の設定例を別 Guardrail として示しています。
    // 実運用では L1 か L2 のどちらか一方を選んでください。
    const guardrailL2 = new Guardrail(this, "GuardrailL2", {
      guardrailName: `support-${envName}-guardrail-l2`,
      blockedInputMessaging: "入力内容を見直してから再試行してください。",
      blockedOutputsMessaging: "安全性の確認ができなかったため回答を控えます。",
      // 日本語対応のため Standard tier（クロスリージョン Guardrail 推論が前提）
      contentFiltersTierConfig: TierConfig.STANDARD,
      topicsTierConfig: TierConfig.STANDARD,
      crossRegionConfig: { guardrailProfileArn },
    });

    guardrailL2.addDeniedTopicFilter({
      name: "competitor-comparison",
      definition: "競合他社の製品との比較や価格情報に関する質問",
      examples: [
        "A社のサービスと比べてどうですか",
        "他社より安くなりませんか",
      ],
    });
    guardrailL2.addDeniedTopicFilter({
      name: "internal-hr",
      definition: "社員の人事情報や給与に関する質問",
    });

    guardrailL2.addContentFilter({
      type: ContentFilterType.HATE,
      inputStrength: ContentFilterStrength.HIGH,
      outputStrength: ContentFilterStrength.HIGH,
    });
    guardrailL2.addContentFilter({
      type: ContentFilterType.INSULTS,
      inputStrength: ContentFilterStrength.HIGH,
      outputStrength: ContentFilterStrength.HIGH,
    });
    guardrailL2.addContentFilter({
      type: ContentFilterType.PROMPT_ATTACK,
      inputStrength: ContentFilterStrength.HIGH,
      outputStrength: ContentFilterStrength.NONE,
    });

    guardrailL2.addPIIFilter({
      type: GeneralPIIType.EMAIL,
      action: GuardrailAction.ANONYMIZE,
      inputAction: GuardrailAction.ANONYMIZE,
      outputAction: GuardrailAction.ANONYMIZE,
    });
    guardrailL2.addPIIFilter({
      type: GeneralPIIType.PHONE,
      action: GuardrailAction.ANONYMIZE,
      inputAction: GuardrailAction.ANONYMIZE,
      outputAction: GuardrailAction.ANONYMIZE,
    });
    guardrailL2.addPIIFilter({
      type: FinancePIIType.CREDIT_DEBIT_CARD_NUMBER,
      action: GuardrailAction.BLOCK,
    });

    // --------------------------------------------------------------
    // 3. アプリケーション推論プロファイル
    // --------------------------------------------------------------
    // アプリケーション × 環境ごとにプロファイルを作り、
    // CloudWatch メトリクスとコスト配賦の単位をそろえる
    const profile = new bedrock.CfnApplicationInferenceProfile(this, "Profile", {
      inferenceProfileName: `support-${envName}`,
      modelSource: { copyFrom: props.modelSourceArn },
      tags: [
        { key: "App", value: "support" },
        { key: "Env", value: envName },
      ],
    });

    this.inferenceProfileArn = profile.attrInferenceProfileArn;

    // --------------------------------------------------------------
    // 4. データソース S3 バケット
    // --------------------------------------------------------------
    // Knowledge Base が参照する S3 バケット。
    // 読者は本書のサンプルデータをこのバケットへ `aws s3 sync` で
    // アップロードしたうえで、Ingestion Job を起動する。
    const docsBucket = new s3.Bucket(this, "DocsBucket", {
      bucketName: undefined, // CDK にユニークな名前を生成させる
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      // 品質ゲートが上書き前の本文・メタデータを復元するため必須。
      versioned: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    this.docsBucketName = docsBucket.bucketName;
    this.docsBucketArn = docsBucket.bucketArn;

    // --------------------------------------------------------------
    // 5. Knowledge Base 用 IAM ロール（Bedrock 信頼）
    // --------------------------------------------------------------
    // 本 Construct で作成する Knowledge Base は 1 つだが、KB ARN を IAM 側に
    // 流し込む循環依存を避けるため Trust Policy は `knowledge-base/*` のままにする
    // （`aws:SourceAccount` を必須にして Confused Deputy は防止）。本番運用で
    // KB を増やしたくないシステムでは、KB 作成後にカスタムリソースで Trust Policy を
    // KB ARN ピン留めに更新するパターンを取る。
    const embedModelArn =
      props.embeddingModelArn ??
      `arn:aws:bedrock:${region}::foundation-model/amazon.titan-embed-text-v2:0`;
    const kbRole = new iam.Role(this, "KbRole", {
      // roleName は付けない（dev/stg/prod を同一アカウントで並行運用するときの
      // 名前衝突を避ける。CDK 自動命名で十分一意になる）
      assumedBy: new iam.ServicePrincipal("bedrock.amazonaws.com", {
        conditions: {
          StringEquals: { "aws:SourceAccount": account },
          ArnLike: {
            "aws:SourceArn": `arn:aws:bedrock:${region}:${account}:knowledge-base/*`,
          },
        },
      }),
      description: `Service role for Chapter 6 sample Knowledge Base (${envName})`,
    });

    docsBucket.grantRead(kbRole);
    kbRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel"],
        resources: [embedModelArn],
      }),
    );
    if (props.enableReranking) {
      const rerankModelArn =
        `arn:aws:bedrock:${region}::foundation-model/cohere.rerank-v3-5:0`;
      kbRole.addToPolicy(
        new iam.PolicyStatement({
          actions: ["bedrock:InvokeModel"],
          resources: [rerankModelArn],
        }),
      );
      // bedrock:Rerankはリソースレベル権限に未対応。モデルは上のInvokeModelで限定する。
      kbRole.addToPolicy(
        new iam.PolicyStatement({
          actions: ["bedrock:Rerank"],
          resources: ["*"],
        }),
      );
    }

    // --------------------------------------------------------------
    // 6. ベクトルストア（S3_VECTORS / OPENSEARCH_SERVERLESS のいずれか）
    // --------------------------------------------------------------
    let storageConfiguration: bedrock.CfnKnowledgeBase.StorageConfigurationProperty;
    let ossIndexDep: aoss.CfnIndex | undefined;

    if (vectorStore === "S3_VECTORS") {
      // ----------------------------------------------------------
      // 6-A. S3 Vectors（デフォルト）
      // ----------------------------------------------------------
      // インフラ常駐費が発生せず、Titan Embed V2（1024次元 cosine）と
      // 相性が良い。サブ秒応答で足りる検証〜中規模 RAG 向け。
      // NestedStackのstackNameは未解決Tokenになるため、文字列加工しない。
      // Constructパス由来のIDを使い、合成時のToken番号に名前を依存させない。
      const stackSlug = cdk.Names.uniqueId(stack).toLowerCase().slice(-16);
      const vectorBucket = new s3vectors.CfnVectorBucket(this, "VectorBucket", {
        vectorBucketName: props.vectorBucketName ?? `support-${envName}-vec-${stackSlug}-${stack.account}`,
      });
      vectorBucket.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);

      const vectorIndex = new s3vectors.CfnIndex(this, "VectorIndex", {
        vectorBucketArn: vectorBucket.attrVectorBucketArn,
        indexName: `support-${envName}-index`,
        dataType: "float32",
        dimension: 1024,
        distanceMetric: "cosine",
        // Bedrock Knowledge Bases 用のS3 Vector Indexでは、次の2キーを
        // non-filterableにして本文とメタデータを2KB上限の対象外にする。
        metadataConfiguration: {
          nonFilterableMetadataKeys: [
            "AMAZON_BEDROCK_TEXT",
            "AMAZON_BEDROCK_METADATA",
          ],
        },
      });
      vectorIndex.addDependency(vectorBucket);
      vectorIndex.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);

      // index 単位の操作と bucket 単位の操作で Statement を分けて least privilege を明示
      kbRole.addToPolicy(
        new iam.PolicyStatement({
          actions: [
            "s3vectors:PutVectors",
            "s3vectors:GetVectors",
            "s3vectors:DeleteVectors",
            "s3vectors:QueryVectors",
            "s3vectors:GetIndex",
          ],
          resources: [vectorIndex.attrIndexArn],
        }),
      );

      storageConfiguration = {
        type: "S3_VECTORS",
        s3VectorsConfiguration: {
          indexArn: vectorIndex.attrIndexArn,
        },
      };
    } else {
      // ----------------------------------------------------------
      // 6-B. OpenSearch Serverless
      // ----------------------------------------------------------
      // 大量データやハイブリッド検索を必要とする本番ワークロード向け。
      // 暗号化ポリシー / ネットワークポリシー / データアクセスポリシー /
      // コレクション / インデックスを CFn だけで構築する。
      const collectionName = `support-${envName}-kb`;

      const encPolicy = new aoss.CfnSecurityPolicy(this, "OssEncPolicy", {
        name: `${collectionName}-enc`,
        type: "encryption",
        policy: JSON.stringify({
          Rules: [
            {
              ResourceType: "collection",
              Resource: [`collection/${collectionName}`],
            },
          ],
          AWSOwnedKey: true,
        }),
      });

      // ネットワークポリシー
      // 既定は閉域（AllowFromPublic=false）。`ossSourceVpcEndpoints` を渡せば
      // その VPC エンドポイントだけを通す。サンドボックス検証を簡単にしたい
      // ときに限り `allowOssPublicAccess: true` を明示で渡して開放する。
      const allowPublic = props.allowOssPublicAccess === true;
      const sourceVpcEndpoints = props.ossSourceVpcEndpoints ?? [];
      if (!allowPublic && sourceVpcEndpoints.length === 0) {
        cdk.Annotations.of(this).addWarning(
          "OpenSearch Serverless network policy is set to private (AllowFromPublic=false) " +
            "but `ossSourceVpcEndpoints` is empty. Bedrock KB ingestion will fail until you " +
            "add at least one VPC endpoint, or set `allowOssPublicAccess: true` for sandbox use.",
        );
      }
      const netPolicy = new aoss.CfnSecurityPolicy(this, "OssNetPolicy", {
        name: `${collectionName}-net`,
        type: "network",
        policy: JSON.stringify([
          {
            Rules: [
              {
                ResourceType: "collection",
                Resource: [`collection/${collectionName}`],
              },
              {
                ResourceType: "dashboard",
                Resource: [`collection/${collectionName}`],
              },
            ],
            AllowFromPublic: allowPublic,
            ...(allowPublic ? {} : { SourceVPCEs: sourceVpcEndpoints }),
          },
        ]),
      });

      const collection = new aoss.CfnCollection(this, "OssCollection", {
        name: collectionName,
        type: "VECTORSEARCH",
        description: `Chapter 6 sample KB vector store (${envName})`,
      });
      collection.addDependency(encPolicy);
      collection.addDependency(netPolicy);

      // CFn の `CfnIndex` は OSS データプレーンの CreateIndex API を呼ぶため、
      // CDK CloudFormation 実行ロールを含めておく必要がある
      const cfnExecRoleArn = `arn:aws:iam::${account}:role/cdk-hnb659fds-cfn-exec-role-${account}-${region}`;
      const ossPrincipals = [
        kbRole.roleArn,
        cfnExecRoleArn,
        ...(props.ossDataAccessPrincipals ?? []),
      ];

      const dataPolicy = new aoss.CfnAccessPolicy(this, "OssDataPolicy", {
        name: `${collectionName}-data`,
        type: "data",
        policy: JSON.stringify([
          {
            Rules: [
              {
                ResourceType: "collection",
                Resource: [`collection/${collectionName}`],
                Permission: ["aoss:*"],
              },
              {
                ResourceType: "index",
                Resource: [`index/${collectionName}/*`],
                Permission: ["aoss:*"],
              },
            ],
            Principal: ossPrincipals,
          },
        ]),
      });
      dataPolicy.addDependency(collection);

      const indexName = `support-${envName}-index`;
      const ossIndex = new aoss.CfnIndex(this, "OssIndex", {
        collectionEndpoint: collection.attrCollectionEndpoint,
        indexName,
        settings: { index: { knn: true, knnAlgoParamEfSearch: 512 } },
        mappings: {
          properties: {
            "bedrock-knowledge-base-default-vector": {
              type: "knn_vector",
              dimension: 1024,
              method: {
                engine: "faiss",
                name: "hnsw",
                spaceType: "l2",
                parameters: { efConstruction: 512, m: 16 },
              },
            },
            AMAZON_BEDROCK_TEXT_CHUNK: { type: "text" },
            AMAZON_BEDROCK_METADATA: { type: "text", index: false },
          },
        },
      });
      ossIndex.addDependency(dataPolicy);

      kbRole.addToPolicy(
        new iam.PolicyStatement({
          actions: ["aoss:APIAccessAll"],
          resources: [collection.attrArn],
        }),
      );

      storageConfiguration = {
        type: "OPENSEARCH_SERVERLESS",
        opensearchServerlessConfiguration: {
          collectionArn: collection.attrArn,
          vectorIndexName: indexName,
          fieldMapping: {
            vectorField: "bedrock-knowledge-base-default-vector",
            textField: "AMAZON_BEDROCK_TEXT_CHUNK",
            metadataField: "AMAZON_BEDROCK_METADATA",
          },
        },
      };
      // KB は OSS インデックスが作成された後にしか作成できない
      ossIndexDep = ossIndex;
    }

    // --------------------------------------------------------------
    // 7. Knowledge Base + Data Source
    // --------------------------------------------------------------
    const knowledgeBase = new bedrock.CfnKnowledgeBase(this, "KnowledgeBase", {
      name: `support-${envName}-kb`,
      description: `Chapter 6 sample KB (${envName}, ${vectorStore})`,
      roleArn: kbRole.roleArn,
      knowledgeBaseConfiguration: {
        type: "VECTOR",
        vectorKnowledgeBaseConfiguration: {
          embeddingModelArn: embedModelArn,
        },
      },
      storageConfiguration,
    });

    // IAM ロールの伝播待ちで CREATE 失敗するのを避けるための明示依存
    knowledgeBase.node.addDependency(kbRole);
    if (ossIndexDep) {
      knowledgeBase.addDependency(ossIndexDep);
    }

    const dataSource = new bedrock.CfnDataSource(this, "DataSource", {
      knowledgeBaseId: knowledgeBase.attrKnowledgeBaseId,
      name: `support-${envName}-s3-source`,
      dataSourceConfiguration: {
        type: "S3",
        s3Configuration: {
          bucketArn: docsBucket.bucketArn,
        },
      },
      vectorIngestionConfiguration: {
        chunkingConfiguration: {
          chunkingStrategy: "FIXED_SIZE",
          fixedSizeChunkingConfiguration: {
            maxTokens: 300,
            overlapPercentage: 20,
          },
        },
      },
    });

    this.knowledgeBaseId = knowledgeBase.attrKnowledgeBaseId;
    this.dataSourceId = dataSource.attrDataSourceId;

    // --------------------------------------------------------------
    // 8. サンプルデータ自動投入 + 初回 Ingestion Job（任意）
    // --------------------------------------------------------------
    // `docsSourceDir` が指定された場合だけ実行する一連の流れ:
    //   1. ローカルディレクトリをデータソースバケットへ BucketDeployment で同期
    //   2. AwsCustomResource で StartIngestionJob を発火
    //      （API は bedrock-agent サービス、IAM Action は `bedrock:StartIngestionJob`）
    //
    // Ingestion Job は非同期に走るので CFn の CREATE_COMPLETE 直後はまだ
    // ベクトル化が完了していない可能性がある（数十秒〜数分）。検索時に
    // 0 件ヒットなら少し待ってから再試行する。
    if (props.docsSourceDir) {
      const deployment = new s3deploy.BucketDeployment(this, "DocsDeployment", {
        sources: [s3deploy.Source.asset(props.docsSourceDir)],
        destinationBucket: docsBucket,
        retainOnDelete: false,
        // コンソールから足したファイルが次回デプロイで消えないように prune は無効化
        prune: false,
        memoryLimit: 512,
      });

      const ingestion = new cr.AwsCustomResource(this, "InitialIngestion", {
        onCreate: {
          service: "@aws-sdk/client-bedrock-agent",
          action: "StartIngestionJob",
          parameters: {
            knowledgeBaseId: knowledgeBase.attrKnowledgeBaseId,
            dataSourceId: dataSource.attrDataSourceId,
            description: `Initial ingestion (${envName}, ${vectorStore})`,
          },
          physicalResourceId: cr.PhysicalResourceId.of(
            `ingest-${envName}-${vectorStore}`,
          ),
        },
        onUpdate: {
          service: "@aws-sdk/client-bedrock-agent",
          action: "StartIngestionJob",
          parameters: {
            knowledgeBaseId: knowledgeBase.attrKnowledgeBaseId,
            dataSourceId: dataSource.attrDataSourceId,
            description: `Update ingestion (${envName}, ${vectorStore})`,
          },
          physicalResourceId: cr.PhysicalResourceId.of(
            `ingest-${envName}-${vectorStore}`,
          ),
        },
        policy: cr.AwsCustomResourcePolicy.fromStatements([
          new iam.PolicyStatement({
            actions: ["bedrock:StartIngestionJob"],
            // 当該 KB に絞って Resource ワイルドカードを残さない
            resources: [
              cdk.Stack.of(this).formatArn({
                service: "bedrock",
                resource: "knowledge-base",
                resourceName: knowledgeBase.attrKnowledgeBaseId,
              }),
            ],
          }),
        ]),
        installLatestAwsSdk: false,
      });
      ingestion.node.addDependency(deployment);
      ingestion.node.addDependency(dataSource);
    }
  }
}
