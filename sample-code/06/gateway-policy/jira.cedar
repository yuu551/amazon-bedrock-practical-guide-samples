// <gateway-arn>を実環境のGateway ARNへ置き換える。

// シニア担当者: Jira起票も許可
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"AtlassianOps___create_jira_ticket",
  resource == AgentCore::Gateway::"<gateway-arn>"
) when {
  principal.hasTag("role") &&
  principal.getTag("role") == "senior-operator"
};
