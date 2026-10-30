// 利用前に <gateway-arn> を実環境のGateway ARNへ置き換える。
// Cedar単独・Dogwood併用のどちらでも使う、Confluence検索だけの許可ポリシー。
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"AtlassianOps___confluence_search",
  resource == AgentCore::Gateway::"<gateway-arn>"
);
