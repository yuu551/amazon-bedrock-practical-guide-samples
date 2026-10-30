# SampleConnect SAML SSO 利用規程

本規程は、SampleConnect Enterpriseプランで提供する SAML 2.0 ベースの Single Sign-On 機能の契約上のルールを定めます。

## 1. 利用条件

SAML SSO は Enterpriseプランの契約者のみが利用できます。Free/Standardプランでは本機能は無効化されています。Enterpriseプラン契約後、初回サインインから48時間以内に IdP 接続情報を登録する必要があります。

## 2. サポートする SAML 仕様

SampleConnect は SAML 2.0 のうち、次のプロファイル・バインディングをサポートします。

- Web Browser SSO Profile（SP-Initiated および IdP-Initiated）
- HTTP-POST バインディング（Request/Response）
- Single Logout Profile（SP-Initiated のみ、HTTP-Redirect バインディング）
- 署名アルゴリズム: RSA-SHA256（RSA-SHA1 は2025年12月で非推奨）
- ダイジェストアルゴリズム: SHA-256

暗号化アサーションはサポートしますが必須ではありません。リプレイ攻撃対策として、`NotOnOrAfter` 属性は必須です。

## 3. SLA

SAML SSO の認証成功率は月次99.95%を保証します。IdP側の障害に起因する失敗は本SLAの対象外です。SLA未達の場合、当月Enterprise利用料の最大15%を返金対象とします。

## 4. ユーザープロビジョニング

SampleConnect は Just-in-Time（JIT）プロビジョニングをデフォルトで有効化します。SAML アサーションに含まれる `email`、`firstName`、`lastName`、`groups` 属性を元に、初回サインイン時にSampleConnectユーザーが自動作成されます。

SCIM 2.0 によるプロビジョニングも対応しますが、JIT との併用はサポートしません。SCIM を有効化する場合、JIT は自動で無効化されます。

## 5. 役割マッピング

IdP側の `groups` 属性とSampleConnectの役割（Owner/Admin/Member/ReadOnly）のマッピングは、管理コンソールで定義します。マッピング未定義のグループに所属するユーザーは、デフォルトで `Member` 役割になります。

## 6. セッション管理

SAML 認証後のSampleConnectセッションは、最大24時間有効です。アイドル時間が4時間を超えると再認証を要求します。これらの値は管理コンソールから1〜24時間の範囲で調整できます。

## 7. 改定履歴

- 2025年9月15日: 制定
- 2025年12月1日: RSA-SHA1 を非推奨化
- 2026年4月1日: SCIM 2.0 サポートを追加
