# SAML SSOでログインできない場合の確認項目

質問: SAML SSOでログインできない場合、最初に何を確認しますか。

回答: まず、IdP側で対象ユーザーがSampleConnect Platform用のSAMLアプリに割り当てられているかを確認します。次に、SampleConnect管理コンソールに表示されるACS URLとEntity ID（SP EntityID）が、IdP側の設定と一致しているかを確認します。あわせて、IdP証明書の有効期限が切れていないかも確認してください。失敗の詳細は、SampleConnect管理コンソールの「SSO設定」→「SAMLログ」から、直近30件のSAMLレスポンスとエラー理由を参照できます。`InvalidNameID`、`SignatureValidationFailed`、`AssertionExpired`のような典型エラーは、SAMLログの該当行をサポート窓口へ共有してください。

補足: IdP側の具体的な設定手順は「SAML認証設定ガイド」、サポート担当者の調査フローは「SAMLトラブルシューティング手順」を参照してください。
