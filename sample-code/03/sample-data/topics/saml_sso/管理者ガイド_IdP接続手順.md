# SAML SSO 管理者ガイド：IdP接続手順

SampleConnect Enterpriseプランの管理者向けに、主要な IdP との SAML 2.0 連携手順をまとめます。

## 接続前の準備

SampleConnect 側で次の値を確認します。管理コンソールの「セキュリティ」→「SAML SSO」から取得できます。

- ACS URL（Assertion Consumer Service）: `https://auth.sampleconnect.example/saml/acs/{tenant_id}`
- Entity ID（SP）: `https://auth.sampleconnect.example/saml/metadata/{tenant_id}`
- Audience Restriction: 上記 Entity ID と同値
- 暗号鍵証明書（公開鍵）: 管理コンソールからダウンロード

## Okta との接続

1. Okta 管理画面で「Applications」→「Create App Integration」→「SAML 2.0」を選択。
2. アプリ名を「SampleConnect」とし、ロゴをアップロード（任意）。
3. SAML 設定画面で次の項目を入力します。
   - Single sign on URL: 上記 ACS URL
   - Audience URI: 上記 Entity ID
   - Name ID format: `EmailAddress`
   - Application username: `Email`
4. Attribute Statements に次の4項目を追加します。
   - `email` → `user.email`
   - `firstName` → `user.firstName`
   - `lastName` → `user.lastName`
   - `groups` → `user.groups` (Filter: Matches regex `.*`)
5. アプリ作成後、「Sign On」タブから IdP メタデータXMLをダウンロード。
6. SampleConnect の管理コンソール「SAML SSO」画面でメタデータXMLをアップロード。
7. テストユーザーで動作確認。

## Microsoft Entra ID（旧 Azure AD）との接続

1. Entra 管理画面で「Enterprise applications」→「New application」→「Create your own application」を選択。
2. アプリ種別は「Integrate any other application you don't find in the gallery」。
3. 作成後、「Single sign-on」→「SAML」を選択。
4. Basic SAML Configuration に SampleConnect の ACS URL と Entity ID を入力。
5. User Attributes & Claims に次のクレームを追加します。
   - `email`: `user.mail`
   - `firstName`: `user.givenname`
   - `lastName`: `user.surname`
   - `groups`: `user.groups` (Group ID または DisplayName を選択)
6. SAML Signing Certificate セクションから「Federation Metadata XML」をダウンロード。
7. SampleConnect 管理コンソールでアップロード。

## Google Workspace との接続

1. Google Admin Console で「Apps」→「Web and mobile apps」→「Add custom SAML app」を選択。
2. App details に「SampleConnect」を入力。
3. Google が発行する「IdP metadata」をダウンロード。
4. Service Provider details に SampleConnect の ACS URL と Entity ID を入力。
5. Attribute mapping で次の4項目を設定します。
   - Primary email → `email`
   - First name → `firstName`
   - Last name → `lastName`
   - グループは Google Workspace ではアサーションに含められないため、SampleConnect 側のロールマッピングは別途手動設定。

## 役割マッピング

SampleConnect 管理コンソールの「セキュリティ」→「SAML SSO」→「Role Mapping」で、IdPグループとSampleConnect役割を紐付けます。

| IdP グループ名 | SampleConnect 役割 |
|---|---|
| `sampleconnect-owners` | Owner |
| `sampleconnect-admins` | Admin |
| `sampleconnect-members` | Member |
| `sampleconnect-readers` | ReadOnly |

マッピング未定義のグループに所属するユーザーは、`Member` 役割で自動作成されます。

## SCIM プロビジョニングの有効化

JIT プロビジョニングではユーザー削除が IdP 側に追従しない（SampleConnect上は残る）ため、本格運用では SCIM 2.0 を推奨します。SCIM を有効化すると JIT は自動的に無効化されます。

SCIM エンドポイント、トークン、サポートする属性は管理コンソールから取得してください。
