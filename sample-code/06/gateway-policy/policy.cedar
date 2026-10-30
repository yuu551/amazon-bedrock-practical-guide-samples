// 説明用に2つの文を併記。CreatePolicyへの登録にはsearch.cedarとjira.cedarを個別に使う。
// 利用前に次の値を実環境に合わせて置き換える。
// - <gateway-arn>: デプロイ後に取得したGateway ARN

// 認証済みの利用者: Confluence検索を許可
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"AtlassianOps___confluence_search",
  resource == AgentCore::Gateway::"<gateway-arn>"
);

// シニア担当者: Jira起票も許可
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"AtlassianOps___create_jira_ticket",
  resource == AgentCore::Gateway::"<gateway-arn>"
) when {
  principal.hasTag("role") &&
  principal.getTag("role") == "senior-operator"
};
