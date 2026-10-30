# SAML SSOで対応しているIdP

質問: SAML SSOで動作確認済みのIdPは何ですか。

回答: SampleConnect Platformでは、Microsoft Entra ID（旧Azure AD）、Okta、Google Workspace、OneLoginの4つを動作確認済みIdPとして公式サポートしています。これらのIdPでは、ユーザー属性のマッピング、グループ連携、Just-In-Time（JIT）プロビジョニングまで一通り検証済みです。一覧外のSAML 2.0準拠IdP（Keycloak、ADFS、PingFederateなど）も技術的には接続可能ですが、サポート窓口での切り分け範囲はSampleConnect Platform側の設定までとなります。

補足: 各IdPの個別設定例は「SAML認証設定ガイド」、属性マッピングの詳細は「IdP連携リファレンス」を参照してください。
