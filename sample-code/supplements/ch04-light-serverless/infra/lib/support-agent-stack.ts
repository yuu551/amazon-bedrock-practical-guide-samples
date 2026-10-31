import * as path from "node:path"
import { fileURLToPath } from "node:url"
import { ContainerImageBuild } from "@cdklabs/deploy-time-build"
import {
  ArnFormat,
  CfnOutput,
  Duration,
  Fn,
  RemovalPolicy,
  Stack,
  type StackProps,
} from "aws-cdk-lib"
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore"
import * as cloudfront from "aws-cdk-lib/aws-cloudfront"
import * as origins from "aws-cdk-lib/aws-cloudfront-origins"
import * as cognito from "aws-cdk-lib/aws-cognito"
import { Platform } from "aws-cdk-lib/aws-ecr-assets"
import * as iam from "aws-cdk-lib/aws-iam"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as s3 from "aws-cdk-lib/aws-s3"
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment"
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager"
import type { Construct } from "constructs"

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..")
const chapter04 = path.resolve(root, "../../04")

export type SupportAgentStackProps = StackProps & {
  // リソース名の接頭辞。同じアカウントで複数の環境を作るときに変える
  prefix: string
  knowledgeBaseId: string
  modelId: string
  sourceBucketName?: string
  sourcePrefixes?: string[]
  // エージェントのデプロイ形式
  //   docker:    ローカルのDockerでARM64のイメージをビルドしてECRへ置く（既定）
  //   zip:       arm64向けの依存ごとzipにして置く。Dockerは不要
  //   codebuild: 第4章と同じく、CodeBuildでARM64のイメージをビルドする
  agentArtifact: "docker" | "zip" | "codebuild"
  // 画面からRuntimeを呼ぶ経路
  //   direct: 画面から直接呼ぶ。RuntimeがCognitoのトークンを検証する（既定。第4章と同じ）
  //   api:    CloudFrontの /api/* をLambda Web Functions上のHonoへ渡し、HonoがIAMでRuntimeを呼ぶ
  agentAccess: "direct" | "api"
  atlassian: {
    baseUrl?: string
    confluenceSpaceKey?: string
    jiraProjectKey?: string
    jiraIssueType?: string
  }
}

export class SupportAgentStack extends Stack {
  constructor(scope: Construct, id: string, props: SupportAgentStackProps) {
    super(scope, id, props)
    const { prefix } = props

    // -------------------------------------------------------------------------
    // 1. ログイン（第4章の defineAuth と同じくメールアドレスでサインアップ）
    // -------------------------------------------------------------------------
    const userPool = new cognito.UserPool(this, "UserPool", {
      selfSignUpEnabled: true,
      signInAliases: { email: true },
      autoVerify: { email: true },
      removalPolicy: RemovalPolicy.DESTROY,
    })
    const userPoolClient = userPool.addClient("WebClient", {
      // adminUserPassword はE2Eと計測で、管理者の認証情報からトークンを取るために使う
      authFlows: { userSrp: true, adminUserPassword: true },
      preventUserExistenceErrors: true,
    })

    // -------------------------------------------------------------------------
    // 2. Gateway・Memory（第4章の amplify/backend.ts と同じ）
    // -------------------------------------------------------------------------
    const atlassianSecret = new secretsmanager.Secret(this, "AtlassianSecret", {
      secretName: `${prefix}/atlassian`,
      // CloudFormationのテンプレートで日本語が化けて毎回差分になったので、説明文は英語にする
      description: "email and API token for Atlassian Cloud (Confluence/Jira)",
      removalPolicy: RemovalPolicy.DESTROY,
    })

    // ATLASSIAN_BASE_URL がなければ、固定の応答を返すダミーをつなぐ。ツールの名前と応答の形は本物と同じ
    const useStub = !props.atlassian.baseUrl
    const atlassianFn = new lambda.Function(this, "AtlassianHandler", {
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: useStub
        ? "stub_gateway_handler.lambda_handler"
        : "atlassian_gateway_handler.lambda_handler",
      code: lambda.Code.fromAsset(
        useStub ? path.join(root, "gateway-stub") : path.join(chapter04, "gateway-lambda"),
        { exclude: ["__pycache__"] },
      ),
      timeout: Duration.seconds(30),
      memorySize: 256,
      environment: {
        ATLASSIAN_BASE_URL: props.atlassian.baseUrl ?? "",
        CONFLUENCE_SPACE_KEY: props.atlassian.confluenceSpaceKey ?? "",
        JIRA_PROJECT_KEY: props.atlassian.jiraProjectKey ?? "",
        JIRA_ISSUE_TYPE: props.atlassian.jiraIssueType ?? "Task",
        ATLASSIAN_SECRET_NAME: atlassianSecret.secretName,
      },
    })
    atlassianSecret.grantRead(atlassianFn)

    const gateway = new agentcore.Gateway(this, "Gateway", {
      gatewayName: `${prefix}-gateway`,
      protocolConfiguration: new agentcore.McpProtocolConfiguration({
        supportedVersions: [agentcore.MCPProtocolVersion.of("2025-11-25")],
        searchType: agentcore.McpGatewaySearchType.SEMANTIC,
      }),
      authorizerConfiguration: agentcore.GatewayAuthorizer.usingAwsIam(),
    })
    const atlassianTarget = gateway.addLambdaTarget("AtlassianTarget", {
      gatewayTargetName: "AtlassianOps",
      lambdaFunction: atlassianFn,
      toolSchema: agentcore.ToolSchema.fromLocalAsset(
        path.join(chapter04, "gateway-lambda/atlassian_tool_schema.json"),
      ),
    })

    const memory = new agentcore.Memory(this, "Memory", {
      memoryName: `${prefix.replace(/[^A-Za-z0-9]/g, "_")}_memory`,
    })

    // GatewayのロールへのIAMポリシーが行き渡る前にTargetを作ると失敗する。
    // cdkdは待たずに作るので、作成に数分かかるMemoryのあとに回す（全体の所要時間は変わらない）
    atlassianTarget.node.addDependency(memory)

    // -------------------------------------------------------------------------
    // 3. AgentCore Runtime。画面から直接呼ぶときはCognitoのトークンで、HonoからはIAMで呼ぶ
    // -------------------------------------------------------------------------
    const image =
      props.agentArtifact === "codebuild"
        ? new ContainerImageBuild(this, "AgentImage", {
            directory: path.join(root, "agent"),
            platform: Platform.LINUX_ARM64,
            exclude: [".build"],
          })
        : undefined
    const agentRuntimeArtifact =
      props.agentArtifact === "zip"
        ? agentcore.AgentRuntimeArtifact.fromCodeAsset({
            path: path.join(root, "agent/.build"),
            runtime: agentcore.AgentCoreRuntime.PYTHON_3_13,
            entrypoint: ["opentelemetry-instrument", "main.py"],
          })
        : image
          ? agentcore.AgentRuntimeArtifact.fromEcrRepository(image.repository, image.imageTag)
          : agentcore.AgentRuntimeArtifact.fromAsset(path.join(root, "agent"), {
              platform: Platform.LINUX_ARM64,
              exclude: [".build"],
            })

    // zipとコンテナは同じRuntimeのまま切り替えられない（Agent artifact type cannot be updated）。
    // デプロイ形式ごとにIDと名前を分け、切り替えたときは別のRuntimeとして作り直す
    const container = props.agentArtifact !== "zip"
    const direct = props.agentAccess === "direct"
    const runtime = new agentcore.Runtime(this, container ? "RuntimeContainer" : "Runtime", {
      runtimeName: `${prefix.replace(/[^A-Za-z0-9]/g, "_")}_agent${container ? "_container" : ""}`,
      agentRuntimeArtifact,
      networkConfiguration: agentcore.RuntimeNetworkConfiguration.usingPublicNetwork(),
      ...(direct
        ? {
            authorizerConfiguration: agentcore.RuntimeAuthorizerConfiguration.usingCognito(userPool, [
              userPoolClient,
            ]),
            // 検証済みの Authorization ヘッダーをエージェントへ渡し、sub から利用者を決める
            requestHeaderConfiguration: { allowlistedHeaders: ["Authorization"] },
          }
        : {}),
      environmentVariables: {
        KNOWLEDGE_BASE_ID: props.knowledgeBaseId,
        MODEL_ID: props.modelId,
        GATEWAY_URL: gateway.gatewayUrl ?? "",
        AGENTCORE_MEMORY_ID: memory.memoryId,
        KNOWLEDGE_BASE_SOURCE_BUCKET_NAME: props.sourceBucketName ?? "",
        KNOWLEDGE_BASE_SOURCE_PREFIXES: (props.sourcePrefixes ?? []).join(","),
      },
    })

    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:Retrieve"],
        resources: [
          this.formatArn({
            service: "bedrock",
            resource: "knowledge-base",
            resourceName: props.knowledgeBaseId,
            arnFormat: ArnFormat.SLASH_RESOURCE_NAME,
          }),
        ],
      }),
    )
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources: ["*"],
      }),
    )
    if (props.sourceBucketName && props.sourcePrefixes?.length) {
      runtime.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ["s3:GetObject"],
          resources: props.sourcePrefixes.map(
            (p) => `arn:aws:s3:::${props.sourceBucketName}/${p}/*`,
          ),
        }),
      )
    }
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock-agentcore:GetMemory",
          "bedrock-agentcore:CreateEvent",
          "bedrock-agentcore:GetEvent",
          "bedrock-agentcore:ListEvents",
          "bedrock-agentcore:ListSessions",
          "bedrock-agentcore:ListActors",
          "bedrock-agentcore:RetrieveMemoryRecords",
          "bedrock-agentcore:ListMemoryRecords",
          "bedrock-agentcore:GetMemoryRecord",
        ],
        resources: [memory.memoryArn],
      }),
    )
    gateway.grantRead(runtime)
    gateway.grantInvoke(runtime)
    if (image) {
      runtime.addToRolePolicy(
        new iam.PolicyStatement({ actions: ["ecr:GetAuthorizationToken"], resources: ["*"] }),
      )
      image.repository.grantPull(runtime)
    }

    // -------------------------------------------------------------------------
    // 4. 画面を置くバケット。CloudFrontからだけ読める
    // -------------------------------------------------------------------------
    const siteBucket = new s3.Bucket(this, "SiteBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    })

    // -------------------------------------------------------------------------
    // 5. CloudFront。/ は S3。api のときだけ /api/* をHonoへ渡す
    // -------------------------------------------------------------------------
    const api = direct ? undefined : this.createHonoApi({ prefix, userPool, userPoolClient, runtime })

    const distribution = new cloudfront.Distribution(this, "Distribution", {
      defaultRootObject: "index.html",
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(siteBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      additionalBehaviors: api ? { "/api/*": api.behavior } : undefined,
    })
    api?.allowFrom(distribution)

    // -------------------------------------------------------------------------
    // 6. 画面の配置。ハッシュ付きのファイルは長くキャッシュし、index.html と config.json はキャッシュしない
    // -------------------------------------------------------------------------
    new s3deploy.BucketDeployment(this, "SiteAssets", {
      sources: [s3deploy.Source.asset(path.join(root, "web/dist"), { exclude: ["index.html"] })],
      destinationBucket: siteBucket,
      cacheControl: [s3deploy.CacheControl.fromString("public, max-age=31536000, immutable")],
      prune: false,
    })
    new s3deploy.BucketDeployment(this, "SiteEntry", {
      sources: [
        s3deploy.Source.asset(path.join(root, "web/dist"), { exclude: ["*", "!index.html"] }),
        s3deploy.Source.jsonData("config.json", {
          region: this.region,
          userPoolId: userPool.userPoolId,
          userPoolClientId: userPoolClient.userPoolClientId,
          // 画面から直接呼ぶときだけ渡す。ないときは /api/chat へ送る
          ...(direct ? { agentRuntimeArn: runtime.agentRuntimeArn } : {}),
        }),
      ],
      destinationBucket: siteBucket,
      cacheControl: [s3deploy.CacheControl.noCache()],
      prune: false,
    })

    new CfnOutput(this, "AppUrl", { value: `https://${distribution.distributionDomainName}` })
    new CfnOutput(this, "UserPoolId", { value: userPool.userPoolId })
    new CfnOutput(this, "UserPoolClientId", { value: userPoolClient.userPoolClientId })
    new CfnOutput(this, "AgentRuntimeArn", { value: runtime.agentRuntimeArn })
    if (api) new CfnOutput(this, "ApiEndpointDomain", { value: api.endpointDomain })
    new CfnOutput(this, "AtlassianSecretName", { value: atlassianSecret.secretName })
  }

  // 発展: 画面とRuntimeの間にLambda Web Functions上のHonoを挟む。L2がないのでL1で書く
  private createHonoApi(props: {
    prefix: string
    userPool: cognito.IUserPool
    userPoolClient: cognito.IUserPoolClient
    runtime: agentcore.Runtime
  }) {
    const { prefix, userPool, userPoolClient, runtime } = props
    const functionName = `${prefix}-api`

    // コード置き場はバージョニングが必須。cdk bootstrapのバケットはAWS管理キー（aws/s3）のSSE-KMSでWeb Functionsから読めないので、SSE-S3で専用に作る
    const codeBucket = new s3.Bucket(this, "ApiCodeBucket", {
      versioned: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    })
    codeBucket.addToResourcePolicy(
      new iam.PolicyStatement({
        principals: [new iam.ServicePrincipal("lambda.amazonaws.com")],
        actions: ["s3:GetObject", "s3:GetObjectVersion"],
        resources: [codeBucket.arnForObjects("*")],
        conditions: {
          StringEquals: { "aws:SourceAccount": this.account },
          ArnLike: {
            "aws:SourceArn": `arn:${this.partition}:lambda:${this.region}:${this.account}:web-function/${functionName}/revision/*`,
          },
        },
      }),
    )

    // api/dist をzipのまま置く。Revisionが古いバージョンを参照するので消さない
    const apiZip = new s3deploy.BucketDeployment(this, "ApiZip", {
      sources: [s3deploy.Source.asset(path.join(root, "api/dist"))],
      destinationBucket: codeBucket,
      destinationKeyPrefix: `${functionName}/`,
      extract: false,
      prune: false,
    })

    const apiRole = new iam.Role(this, "ApiRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AWSLambdaBasicExecutionRole"),
      ],
    })
    runtime.grantInvokeRuntime(apiRole)

    const webFunction = new lambda.CfnWebFunction(this, "ApiFunction", { functionName })
    const revision = new lambda.CfnWebFunctionRevision(this, "ApiRevision", {
      // ARNではなく素の名前を渡す
      functionName,
      buildConfig: {
        codeConfig: {
          s3Object: {
            bucket: codeBucket.bucketName,
            key: Fn.join("", [`${functionName}/`, Fn.select(0, apiZip.objectKeys)]),
            versionId: Fn.select(0, apiZip.objectVersionIds),
          },
        },
        runtimeConfig: { runtime: "nodejs24.x" },
      },
      serviceConfig: {
        executionRoleArn: apiRole.roleArn,
        timeoutSeconds: 300,
        environmentVariables: {
          USER_POOL_ID: userPool.userPoolId,
          USER_POOL_CLIENT_ID: userPoolClient.userPoolClientId,
          AGENT_RUNTIME_ARN: runtime.agentRuntimeArn,
          NODE_OPTIONS: "--enable-source-maps",
        },
      },
    })
    revision.node.addDependency(webFunction, codeBucket.policy!, apiRole)

    const endpoint = new lambda.CfnWebFunctionEndpoint(this, "ApiEndpoint", {
      functionName,
      endpointName: "live",
      endpointType: "HomeRegion",
      // CloudFrontのOACで署名したリクエストだけを受ける
      authType: "IamAuth",
      revisionWeights: [{ revisionId: revision.attrRevisionId, weight: 100 }],
    })
    endpoint.node.addDependency(webFunction)

    const apiOac = new cloudfront.CfnOriginAccessControl(this, "ApiOac", {
      originAccessControlConfig: {
        name: `${functionName}-oac`,
        originAccessControlOriginType: "lambda",
        signingBehavior: "always-amz-auth",
        signingProtocol: "sigv4",
      },
    })

    const behavior: cloudfront.BehaviorOptions = {
      origin: new origins.HttpOrigin(endpoint.attrDomainName, {
        protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
        originAccessControlId: apiOac.attrId,
        // ツール呼び出しの間は文字が流れないことがあるので、待つ時間を上限まで延ばす
        readTimeout: Duration.seconds(60),
      }),
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
      cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
      originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
      // SSEを少しずつ届けるため、CloudFrontで圧縮しない
      compress: false,
    }

    return {
      behavior,
      endpointDomain: endpoint.attrDomainName,
      // CloudFrontからだけ呼べるようにする
      allowFrom: (distribution: cloudfront.Distribution) => {
        new lambda.CfnResourcePolicy(this, "ApiInvokeFromCloudFront", {
          resourceArn: webFunction.attrFunctionArn,
          policyDocument: {
            Version: "2012-10-17",
            Statement: [
              {
                Effect: "Allow",
                Principal: { Service: "cloudfront.amazonaws.com" },
                Action: "lambda:InvokeWebFunctionEndpoint",
                Resource: endpoint.attrEndpointArn,
                Condition: {
                  StringEquals: {
                    "aws:SourceAccount": this.account,
                    "aws:SourceArn": distribution.distributionArn,
                  },
                },
              },
            ],
          },
        })
      },
    }
  }
}
