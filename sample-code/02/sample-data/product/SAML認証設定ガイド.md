# SAML認証設定ガイド

## 概要

本ガイドでは、SampleConnect PlatformでSAML 2.0によるシングルサインオン（SSO）を設定する手順を説明します。SAML認証を有効化することで、企業の既存のIdP（Identity Provider）を利用した一元的な認証管理が実現できます。

## 前提条件

SAML認証の設定には以下の条件を満たしている必要があります。

- SampleConnect PlatformのEnterpriseプランを契約していること
- SampleConnect管理画面で「組織管理者」ロールを持つアカウントでログインしていること
- 連携先のIdPの管理者権限を持っていること

対応しているIdPは以下のとおりです。

- Microsoft Entra ID（旧Azure AD）
- Okta
- Google Workspace
- OneLogin
- その他SAML 2.0準拠のIdP

## 設定手順

### ステップ1: SSO設定画面を開く

SampleConnect管理画面にログインし、左メニューから「セキュリティ」→「SSO設定」を選択します。SSO設定画面が表示されたら「SAML 2.0」タブをクリックしてください。

### ステップ2: SampleConnect側のSAML情報を確認する

SSO設定画面に表示される以下の情報を控えてください。IdP側でSAMLアプリケーションを作成する際に必要になります。

- **Entity ID（発行者）**: `https://auth.sampleconnect.example.co.jp/saml/metadata/{テナントID}`
- **ACS URL（Assertion Consumer Service URL）**: `https://auth.sampleconnect.example.co.jp/saml/acs/{テナントID}`
- **SLO URL（Single Logout URL）**: `https://auth.sampleconnect.example.co.jp/saml/slo/{テナントID}`

「メタデータXMLをダウンロード」ボタンから、SampleConnect側のSPメタデータをXML形式でダウンロードすることもできます。IdPがメタデータのインポートに対応している場合は、このファイルを使用すると設定が簡略化されます。

### ステップ3: IdP側でSAMLアプリケーションを作成する

IdPの管理画面で新しいSAMLアプリケーションを作成し、ステップ2で確認したEntity IDとACS URLを入力します。IdPごとの設定方法は以下を参考にしてください。

**Microsoft Entra IDの場合**: 「エンタープライズ アプリケーション」→「新しいアプリケーション」→「独自のアプリケーションの作成」から設定します。「シングルサインオン」→「SAML」を選択し、基本的なSAML構成にEntity IDとACS URLを入力してください。

**Oktaの場合**: 「Applications」→「Create App Integration」→「SAML 2.0」を選択し、Single sign-on URLにACS URL、Audience URI（SP Entity ID）にEntity IDを入力してください。

**Google Workspaceの場合**: 管理コンソールの「アプリ」→「ウェブアプリとモバイルアプリ」→「アプリを追加」→「カスタムSAMLアプリの追加」から設定します。

### ステップ4: IdPメタデータをSampleConnectに登録する

IdP側の設定が完了したら、SampleConnect管理画面のSSO設定画面に戻り、IdPの情報を登録します。登録方法は2通りあります。

**メタデータXMLによる一括登録（推奨）**: 「IdPメタデータXMLをアップロード」からIdPが提供するメタデータXMLファイルをアップロードします。必要な情報が自動的に読み取られます。2026年4月のアップデートにより、メタデータURLを指定して自動取得する機能も追加されました。

**手動登録**: 以下の情報を個別に入力します。

- **IdP Entity ID**: IdPの識別子URL
- **IdP SSO URL**: IdPのSAMLログインエンドポイントURL
- **IdP証明書**: IdPが発行するX.509署名証明書（PEM形式）

### ステップ5: 属性マッピングを設定する

IdPからSampleConnectに送信されるSAMLアサーションの属性をマッピングします。以下の属性は必須です。

| SAML属性名 | SampleConnectフィールド | 説明 |
|---|---|---|
| email | メールアドレス | ユーザーの一意識別に使用（必須） |
| displayName | 表示名 | ユーザーの表示名（必須） |
| department | 部署 | 組織内の部署名（任意） |
| role | ロール | SampleConnect上のロール（任意） |

属性名はIdPの設定に合わせてカスタマイズ可能です。たとえばMicrosoft Entra IDでは`user.mail`、`user.displayname`、`user.department`といった属性名が使われます。

### ステップ6: 接続テストを実施する

「テスト接続」ボタンをクリックして、IdPとの接続テストを実施します。テスト用のIdPアカウントでログインし、SampleConnect側でユーザー情報が正しく取得されることを確認してください。テスト結果は画面上に表示され、属性の受信状況やエラーの詳細を確認できます。

### ステップ7: 強制SSOの有効化（オプション）

すべてのユーザーにSAML認証を強制する場合は、「強制SSOを有効化」をオンにします。この設定を有効にすると、パスワードによる直接ログインが無効化され、IdP経由でのログインのみが許可されます。

緊急時のアクセス手段として、組織管理者にはバイパスURLが提供されます。このURLは「SSO設定」→「緊急アクセス」から確認できます。IdPの障害時などにこのURLを使用してログインしてください。

## トラブルシューティング

### 証明書の期限切れ

IdP証明書の有効期限が切れると、SAML認証が失敗します。SampleConnectは証明書の有効期限を監視し、期限の30日前から管理者にメール通知を送信します。証明書を更新する場合は、IdP側で新しい証明書を発行し、SampleConnect管理画面で証明書を差し替えてください。

### 属性マッピングの不一致

SAMLアサーションに必須属性（emailまたはdisplayName）が含まれていない場合、ログインに失敗します。「SSO設定」→「SAMLログ」から直近のSAMLレスポンスを確認し、IdP側の属性名とSampleConnect側のマッピング設定が一致しているか確認してください。

### リダイレクトループ

ログイン時にリダイレクトが繰り返される場合、ACS URLの設定に誤りがある可能性があります。IdP側に登録したACS URLがSampleConnect管理画面に表示されているURLと完全に一致しているか確認してください。末尾のスラッシュの有無やHTTPS指定の違いも原因となります。

ログインの問題が解決しない場合は、enterprise-support@sampleconnect.example.co.jpまでお問い合わせください。
