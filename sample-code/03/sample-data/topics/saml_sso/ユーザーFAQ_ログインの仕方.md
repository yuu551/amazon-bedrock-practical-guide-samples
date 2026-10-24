# SAML SSO ユーザーFAQ：ログインの仕方

SampleConnect Enterpriseプランで SAML SSO が有効化されたテナントを利用するエンドユーザー向けの FAQ です。

## Q. SSO 有効化後、これまでのパスワードでログインできなくなりました

Enterpriseプランで SAML SSO を有効化すると、それ以前のパスワード認証は無効化されます。会社の IdP（Okta、Microsoft Entra ID、Google Workspace 等）のアカウントでサインインしてください。サインイン画面でメールアドレスを入力すると、自動的に IdP のサインインページにリダイレクトされます。

緊急時のローカルパスワード認証は、Owner 役割のユーザー1名のみ「Break Glass アカウント」として残されます。通常運用では使用しません。

## Q. ログイン画面でループするのですが

IdP 側で SampleConnect アプリへのアクセスが許可されていない可能性があります。具体的には次の3つを確認してください。

1. IdP 管理者があなたのアカウントを SampleConnect アプリに割り当てているか
2. IdP の `groups` クレームが正しく設定されているか
3. ブラウザの Cookie がブロックされていないか（SampleConnect は HTTP-POST バインディングを使うため、サードパーティCookieは不要だが、ローカルストレージは必須）

## Q. SP-Initiated と IdP-Initiated のどちらでサインインすべきですか

SampleConnect は両方をサポートします。

- **SP-Initiated**: SampleConnect のサインインURL（`https://app.sampleconnect.example/login`）にアクセスし、メールアドレスを入力するとIdPにリダイレクトされます。最も一般的なフローです。
- **IdP-Initiated**: IdP のダッシュボード（Okta End User Dashboard 等）から SampleConnect アイコンをクリックすると、直接サインインできます。

通常は SP-Initiated を推奨します。IdP-Initiated はリンクをブックマーク化したい場合に便利ですが、Deep Link が使えない（SampleConnect内の特定画面に直接遷移できない）制約があります。

## Q. ログアウトするとどうなりますか

SampleConnect のログアウトボタンを押すと、SampleConnect セッションは即時破棄されます。Single Logout（SLO）が有効な場合、IdP 側のセッションも同時にログアウトされます。

SLO が無効の場合、SampleConnect からはログアウトされますが、IdP 側のセッションは残ります。同じブラウザで再度サインインしようとすると、IdP のセッション期限まで自動的にSampleConnectに再ログインされます。

## Q. どの IdP 属性が SampleConnect に渡されますか

SampleConnect は次の SAML 属性を必須・任意で受け取ります。

- 必須: `email`、`firstName`、`lastName`
- 任意: `groups`（役割マッピング用）、`department`、`employeeId`

これらの値は IdP の管理者が設定します。属性が更新された場合、次回サインイン時に SampleConnect 側のユーザー情報も更新されます。

## Q. サインイン時に「SAMLResponse の署名検証に失敗しました」と表示されます

IdP 側で発行される SAML Response の署名アルゴリズムが古い可能性があります。SampleConnect は2025年12月から RSA-SHA1 を非推奨化しており、RSA-SHA256 のみ受け付けます。IdP の管理者に署名アルゴリズムを RSA-SHA256 に変更するよう依頼してください。

Microsoft Entra ID の場合は「Single sign-on」→「SAML Signing Certificate」→「Edit」→「Signing Algorithm」で変更できます。
