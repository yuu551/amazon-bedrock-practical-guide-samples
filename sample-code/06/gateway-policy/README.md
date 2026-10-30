# Gateway Policyサンプル

`policy.cedar`は、JWTの`role`クレームでGatewayツールの利用範囲を分ける例です。APIへの登録には、検索許可の`search.cedar`と起票許可の`jira.cedar`へ分けたファイルを使います。`sequence.dogwood`は、その制御に「30分以内にConfluence検索を完了していること」という実行順の条件を加えます。どちらもGatewayターゲット名`AtlassianOps`と、`gateway-lambda/atlassian_tool_schema.json`の`confluence_search`・`create_jira_ticket`を使います。

第6章のメインサンプルはIAM認証です。このポリシーを試す場合はGatewayをJWT認証へ変更し、利用者のJWTをGatewayへ渡してください。CDKデプロイでは自動適用されません。

## 適用前の準備

次のものを用意します。

- デプロイ済みのGateway（このサンプルではJWT認証へ変更したもの）
- `role`クレームを含むJWT
- AWS CLI v2（本手順は2.36.19で確認）と`jq`
- Gatewayと同じAWSアカウント・リージョンを操作できる認証情報

最初の`cd`はリポジトリのルートから実行し、それ以降は同じターミナルで続けます。AWsumeなどで認証情報を環境変数へ設定している場合、`--profile`は不要です。

```shell
cd sample-code/06/gateway-policy

REGION=us-east-1
GATEWAY_ID=<gateway-id>

GATEWAY_JSON=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier "$GATEWAY_ID" \
  --region "$REGION")
GATEWAY_ARN=$(printf '%s' "$GATEWAY_JSON" | jq -r '.gatewayArn')
GATEWAY_NAME=$(printf '%s' "$GATEWAY_JSON" | jq -r '.name')
GATEWAY_ROLE_ARN=$(printf '%s' "$GATEWAY_JSON" | jq -r '.roleArn')
AUTHORIZER_TYPE=$(printf '%s' "$GATEWAY_JSON" | jq -r '.authorizerType')
AUTHORIZER_CONFIGURATION=$(printf '%s' "$GATEWAY_JSON" | jq -c '.authorizerConfiguration')
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
```

Gateway ARNはデプロイ後に決まります。このサンプルは対象のGateway ARNをポリシーへ明記するため、Gatewayを先に作成してください。

第4章のCDKサンプルはIAM認証でGatewayを作ります。JWT認証へ変更する場合は、利用するIdPのDiscovery URLとクライアントIDから、次の値を設定します。Amazon Cognitoを使う場合の例です。

```shell
USER_POOL_ID=<user-pool-id>
JWT_CLIENT_ID=<user-pool-client-id>
JWT_DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/openid-configuration"

AUTHORIZER_TYPE=CUSTOM_JWT
AUTHORIZER_CONFIGURATION=$(jq -n \
  --arg discoveryUrl "$JWT_DISCOVERY_URL" \
  --arg clientId "$JWT_CLIENT_ID" \
  '{customJWTAuthorizer: {discoveryUrl: $discoveryUrl, allowedClients: [$clientId]}}')
```

この変更後は、IAM署名ではなくJWTをGatewayへ渡します。`role`クレームをトークンへ追加する方法はIdPごとに異なるため、実際に発行したトークンのクレームを確認してからポリシーを適用してください。

## 必要なIAM権限

権限を付ける相手は、Gateway実行ロール、構築コマンドを実行する管理者、Gatewayを呼び出すIAMロールの3者です。

### Gateway実行ロール

Policy Engineを使うGateway実行ロールには、少なくとも次の3権限が必要です。

- `bedrock-agentcore:AuthorizeAction`
- `bedrock-agentcore:PartiallyAuthorizeActions`
- `bedrock-agentcore:GetPolicyEngine`

Dogwoodの時間・順序制御を使う場合は、さらに`bedrock-agentcore:GetWorkloadAccessToken`が必要です。`gateway-execution-policy.json`には4権限をまとめています。Policy Engineを作成してIDを取得したあと、プレースホルダーを置き換えてGateway実行ロールへ追加します。

```shell
POLICY_ENGINE_ID=$(aws bedrock-agentcore-control create-policy-engine \
  --name SupportGatewayPolicyEngine \
  --description "Policy engine for the support gateway" \
  --region "$REGION" \
  --query policyEngineId \
  --output text)

aws bedrock-agentcore-control wait policy-engine-active \
  --policy-engine-id "$POLICY_ENGINE_ID" \
  --region "$REGION"

POLICY_ENGINE_ARN=$(aws bedrock-agentcore-control get-policy-engine \
  --policy-engine-id "$POLICY_ENGINE_ID" \
  --region "$REGION" \
  --query policyEngineArn \
  --output text)

sed \
  -e "s|<region>|$REGION|g" \
  -e "s|<account-id>|$ACCOUNT_ID|g" \
  -e "s|<gateway-id>|$GATEWAY_ID|g" \
  -e "s|<policy-engine-id>|$POLICY_ENGINE_ID|g" \
  gateway-execution-policy.json \
  > /tmp/gateway-execution-policy.json

GATEWAY_ROLE_NAME=${GATEWAY_ROLE_ARN##*/}
aws iam put-role-policy \
  --role-name "$GATEWAY_ROLE_NAME" \
  --policy-name GatewayPolicyEnginePermissions \
  --policy-document file:///tmp/gateway-execution-policy.json
```

`GetWorkloadAccessToken`のステートメントはDogwoodを使う場合だけ必要です。Cedarのみを使う場合は、そのステートメントを削除できます。LambdaなどGatewayターゲットを呼ぶための既存権限は、このポリシーとは別に残してください。

### 設定コマンドを実行する管理者

このREADMEのコマンドを実行するIAMユーザーまたはIAMロールには、次の権限が必要です。

- `bedrock-agentcore:CreatePolicyEngine`、`bedrock-agentcore:GetPolicyEngine`
- `bedrock-agentcore:CreatePolicy`、`bedrock-agentcore:GetPolicy`
- `bedrock-agentcore:GetGateway`、`bedrock-agentcore:UpdateGateway`
- `bedrock-agentcore:ManageResourceScopedPolicy`
- `bedrock-agentcore:InvokeGateway`
- Gateway実行ロールに対する`iam:PassRole`
- 上のインラインポリシーも同じ認証情報で追加する場合は`iam:PutRolePolicy`

`CreatePolicy`は、CedarやDogwoodに書いたアクションがGatewayに存在するかを検証します。この検証でも`bedrock-agentcore:InvokeGateway`を使うため、権限がないと作成リクエストを受け付けたあとにポリシーが`CREATE_FAILED`になります。

検証用であればAWS管理ポリシーの`BedrockAgentCoreFullAccess`も使えます。あわせて、対象のGateway実行ロールに対する`iam:PassRole`と、インラインポリシーを追加する場合の`iam:PutRolePolicy`が許可されているかを確認してください。本番では、[AWS公式のResource Management Roleの例](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html)をもとに対象ARNを絞ります。

### Gatewayを呼び出すIAMロール

IAM認証でGatewayを呼ぶRuntimeやLambdaには、Gateway ARNを対象とした`bedrock-agentcore:InvokeGateway`が必要です。第4章のCDKサンプルでは`gateway.grantInvoke(runtime)`でRuntimeへ付与しています。別の呼び出し元を追加する場合は、同じ権限をそのIAMロールへ付けてください。

JWT認証で呼ぶ利用者はJWTで認証されるため、このIAM権限を利用者へ付ける必要はありません。前項のとおり、ポリシーを作成する管理者には認証方式にかかわらず`InvokeGateway`が必要です。

## Policy EngineをGatewayへ関連付ける

既存Gatewayの名前、実行ロール、認証設定を保持したまま、まず`LOG_ONLY`で関連付けます。`AUTHORIZER_TYPE`が`CUSTOM_JWT`であることも確認してください。

```shell
aws bedrock-agentcore-control update-gateway \
  --gateway-identifier "$GATEWAY_ID" \
  --name "$GATEWAY_NAME" \
  --role-arn "$GATEWAY_ROLE_ARN" \
  --protocol-type MCP \
  --authorizer-type "$AUTHORIZER_TYPE" \
  --authorizer-configuration "$AUTHORIZER_CONFIGURATION" \
  --policy-engine-configuration "arn=$POLICY_ENGINE_ARN,mode=LOG_ONLY" \
  --region "$REGION"
```

`InternalServerException`になる場合は、Gateway実行ロールに`AuthorizeAction`、`PartiallyAuthorizeActions`、`GetPolicyEngine`の3権限があり、Policy EngineとGatewayの両方のARNが許可対象に入っているかを確認してください。

## ポリシーを登録する

ソース中の`<gateway-arn>`を実際のARNへ置き換えた一時ファイルを作ります。変換後のファイルを`/tmp/`へ保存します。

```shell
sed "s|<gateway-arn>|$GATEWAY_ARN|g" jira.cedar > /tmp/jira.cedar
sed "s|<gateway-arn>|$GATEWAY_ARN|g" search.cedar > /tmp/search.cedar
sed "s|<gateway-arn>|$GATEWAY_ARN|g" sequence.dogwood > /tmp/sequence.dogwood
```

### Cedarのみを使う場合

`CreatePolicy`へ渡せるCedar文は1つです。検索と起票の許可を別々に登録します。個別ポリシーは`ACTIVE`にし、Gateway側の`LOG_ONLY`で判定結果だけを記録します。

検索ルールは「認証済みの利用者全員に検索を許可する」という意図のため、条件が広いことを示す`Overly Permissive`の検証結果が出ます。この内容を確認したうえで、検索ルールだけ`IGNORE_ALL_FINDINGS`を指定します。起票ルールは`FAIL_ON_ANY_FINDINGS`で検証します。ほかのポリシーの失敗を回避するために、一律で検証を省略しないでください。

```shell
SEARCH_DEFINITION=$(jq -n \
  --rawfile statement /tmp/search.cedar \
  '{cedar: {statement: $statement}}')
JIRA_DEFINITION=$(jq -n \
  --rawfile statement /tmp/jira.cedar \
  '{cedar: {statement: $statement}}')

SEARCH_POLICY_ID=$(aws bedrock-agentcore-control create-policy \
  --policy-engine-id "$POLICY_ENGINE_ID" \
  --name SupportSearchPolicy \
  --definition "$SEARCH_DEFINITION" \
  --validation-mode IGNORE_ALL_FINDINGS \
  --enforcement-mode ACTIVE \
  --region "$REGION" \
  --query policyId --output text)

JIRA_POLICY_ID=$(aws bedrock-agentcore-control create-policy \
  --policy-engine-id "$POLICY_ENGINE_ID" \
  --name SupportJiraPolicy \
  --definition "$JIRA_DEFINITION" \
  --validation-mode FAIL_ON_ANY_FINDINGS \
  --enforcement-mode ACTIVE \
  --region "$REGION" \
  --query policyId --output text)

for POLICY_ID in "$SEARCH_POLICY_ID" "$JIRA_POLICY_ID"; do
  aws bedrock-agentcore-control wait policy-active \
    --policy-engine-id "$POLICY_ENGINE_ID" \
    --policy-id "$POLICY_ID" \
    --region "$REGION"
done
```

### Dogwoodの順序制御を使う場合

`policy.cedar`をそのまま登録すると、Jira起票用のCedar `permit`によって順序制御を迂回します。Cedarのみの構成から切り替える場合は、先に起票許可のポリシーを削除してください。代わりに、Confluence検索だけを許可する`search.cedar`と、検索後のJira起票を許可する`sequence.dogwood`を登録します。以下は新しいPolicy Engineへ登録する例です。検索ポリシーがすでに登録済みなら、そのIDを使い、検索ポリシーの再作成は省略してください。

```shell
SEARCH_DEFINITION=$(jq -n \
  --rawfile statement /tmp/search.cedar \
  '{cedar: {statement: $statement}}')

SEARCH_POLICY_ID=$(aws bedrock-agentcore-control create-policy \
  --policy-engine-id "$POLICY_ENGINE_ID" \
  --name SupportSearchPolicy \
  --definition "$SEARCH_DEFINITION" \
  --validation-mode IGNORE_ALL_FINDINGS \
  --enforcement-mode ACTIVE \
  --region "$REGION" \
  --query policyId \
  --output text)

DOGWOOD_DEFINITION=$(jq -n \
  --rawfile statement /tmp/sequence.dogwood \
  '{policy: {statement: $statement}}')

DOGWOOD_POLICY_ID=$(aws bedrock-agentcore-control create-policy \
  --policy-engine-id "$POLICY_ENGINE_ID" \
  --name SupportSequencePolicy \
  --definition "$DOGWOOD_DEFINITION" \
  --validation-mode FAIL_ON_ANY_FINDINGS \
  --enforcement-mode ACTIVE \
  --region "$REGION" \
  --query policyId \
  --output text)

aws bedrock-agentcore-control wait policy-active \
  --policy-engine-id "$POLICY_ENGINE_ID" \
  --policy-id "$SEARCH_POLICY_ID" \
  --region "$REGION"

aws bedrock-agentcore-control wait policy-active \
  --policy-engine-id "$POLICY_ENGINE_ID" \
  --policy-id "$DOGWOOD_POLICY_ID" \
  --region "$REGION"
```

## ポリシーの動作

- JWT認証済みの利用者は`AtlassianOps___confluence_search`を利用できる
- `role`が`senior-operator`の利用者は`AtlassianOps___create_jira_ticket`も利用できる
- それ以外のツール呼び出しは、Cedarのデフォルト拒否によって拒否される

Dogwoodの順序制御を使う場合は、`policy.cedar`のJira起票用`permit`を`sequence.dogwood`のルールに置き換えてください。両方を登録すると、Cedar側の`permit`だけで起票が許可され、順序制御を迂回します。Confluence検索とJira起票の呼び出しには同じ`x-amzn-bedrock-agentcore-policy-session-id`を渡します。

実際のリクエストで判定結果を確認できたら、同じ`update-gateway`コマンドの`mode=LOG_ONLY`を`mode=ENFORCE`へ変更して適用します。Dogwoodを使う場合は、既存セッションと重複しない新しいポリシーセッションIDで試してください。

権限とコマンドの仕様は、次のAWS公式ドキュメントも参照してください。

- [GatewayとPolicy Engineに必要なIAM権限](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html)
- [既存GatewayへPolicy Engineを関連付ける](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/update-gateway-with-policy.html)
- [Dogwoodの時間・順序ポリシーを作成する](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-temporal-authoring.html)

[第6章の構成一覧に戻る](../README.md)
