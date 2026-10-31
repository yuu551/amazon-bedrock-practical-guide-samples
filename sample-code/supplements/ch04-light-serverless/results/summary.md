| 方式・エージェントのデプロイ形式 | 変更 | 回数 | 成功 | コマンド全体（秒） | デプロイ本体（秒） | 反映を確認できるまで（秒） |
|---|---|---|---|---|---|---|
| Amplify Hosting（第4章） | 初回作成 | 1 | 1 | 474 | - | - |
| Amplify Hosting（第4章） | 変更なしの再ビルド | 3 | 3 | 163（160〜214） | - | - |
| Amplify Hosting（第4章） | 画面 | 3 | 3 | 200（155〜220） | - | - |
| Amplify Hosting（第4章） | エージェント | 3 | 3 | 354（252〜492） | - | - |
| Amplify sandbox（第4章） | 初回作成 | 1 | 1 | 286 | - | - |
| Amplify sandbox（第4章） | エージェント | 3 | 3 | 164（143〜176） | - | - |
| Amplify sandbox（第4章） | 削除 | 1 | 1 | 543 | - | - |
| cdk deploy・Docker・画面から直接呼ぶ | 初回作成 | 1 | 1 | 268 | 220 | - |
| cdk deploy・Docker・画面から直接呼ぶ | 画面 | 3 | 3 | 78（71〜78） | 21（21〜22） | 79（72〜79） |
| cdk deploy・Docker・画面から直接呼ぶ | エージェント | 3 | 3 | 77（74〜78） | 22（21〜22） | 87（85〜88） |
| cdk deploy・Docker・画面から直接呼ぶ | エージェントのデプロイ形式の切り替え | 1 | 1 | 169 | 62 | - |
| cdkd deploy・Docker・画面から直接呼ぶ | 初回作成 | 1 | 1 | 256 | 204 | - |
| cdkd deploy・Docker・画面から直接呼ぶ | 画面 | 3 | 3 | 54（49〜62） | 17（16〜18） | 54（50〜62） |
| cdkd deploy・Docker・画面から直接呼ぶ | エージェント | 3 | 3 | 46（45〜46） | 17（17〜18） | 56（56〜58） |
| cdkd deploy・Docker・画面から直接呼ぶ | エージェントのデプロイ形式の切り替え | 1 | 1 | 75 | 34 | - |
| cdk deploy・CodeBuild・画面から直接呼ぶ | エージェント | 3 | 3 | 117（116〜120） | 72（72〜78） | 130（127〜132） |
| cdk deploy・CodeBuild・画面から直接呼ぶ | エージェントのデプロイ形式の切り替え | 1 | 1 | 186 | 141 | - |
| cdkd deploy・CodeBuild・画面から直接呼ぶ | エージェント | 3 | 3 | 92（91〜116） | 67（66〜89） | 102（102〜126） |
| cdkd deploy・CodeBuild・画面から直接呼ぶ | エージェントのデプロイ形式の切り替え | 1 | 1 | 140 | 115 | - |
| GitHub Actions（main）・cdk deploy・画面から直接呼ぶ | 画面 | 3 | 3 | 79（72〜79） | - | 91（91〜101） |
| GitHub Actions（main）・cdk deploy・画面から直接呼ぶ | エージェント | 4 | 3 | 105（102〜111） | - | 136（130〜139） |
| GitHub Actions（develop）・cdkd deploy・画面から直接呼ぶ | 画面 | 3 | 3 | 74（72〜80） | - | 91（91〜102） |
| GitHub Actions（develop）・cdkd deploy・画面から直接呼ぶ | エージェント | 5 | 3 | 81（77〜99） | - | 114（101〜125） |
| cdk deploy・Docker | 画面 | 3 | 3 | 72（71〜73） | 22（21〜22） | 73（71〜74） |
| cdk deploy・Docker | API | 3 | 3 | 88（84〜95） | 40（39〜45） | 90（86〜96） |
| cdk deploy・Docker | エージェント | 3 | 3 | 82（79〜90） | 28（27〜28） | 93（89〜99） |
| cdk deploy・Docker | エージェントのデプロイ形式の切り替え | 1 | 1 | 175 | 119 | - |
| cdk deploy・zip | 初回作成 | 1 | 1 | 505 | 440 | - |
| cdk deploy・zip | 画面 | 3 | 3 | 77（74〜78） | 22（21〜22） | 78（75〜79） |
| cdk deploy・zip | API | 3 | 3 | 94（93〜104） | 44（44〜45） | 95（94〜105） |
| cdk deploy・zip | エージェント | 3 | 3 | 116（113〜117） | 39（38〜40） | 127（121〜128） |
| cdk deploy・zip | 削除 | 1 | 1 | 386 | - | - |
| cdk deploy・CodeBuild | エージェント | 4 | 3 | 149（148〜198） | 80（79〜130） | 161（159〜210） |
| cdk deploy・CodeBuild | エージェントのデプロイ形式の切り替え | 1 | 1 | 246 | 178 | - |
| cdk deploy --hotswap-fallback・zip | 画面 | 3 | 3 | 63（61〜77） | 15（14〜19） | 64（62〜78） |
| cdk deploy --hotswap-fallback・zip | エージェント | 3 | 3 | 84（84〜94） | 10（9〜10） | 106（103〜114） |
| cdk deploy --express・zip | 初回作成 | 1 | 1 | 462 | 417 | - |
| cdk deploy --express・zip | 画面 | 3 | 3 | 82（76〜86） | 27（21〜28） | 83（77〜87） |
| cdk deploy --express・zip | API | 3 | 0 | - | - | - |
| cdkd deploy・Docker | 初回作成 | 1 | 1 | 313 | 185 | - |
| cdkd deploy・Docker | 画面 | 3 | 3 | 44（43〜46） | 16（16〜17） | 45（44〜46） |
| cdkd deploy・Docker | API | 3 | 3 | 51（50〜52） | 26（26〜27） | 52（51〜53） |
| cdkd deploy・Docker | エージェント | 3 | 3 | 38（38〜39） | 9（9〜9） | 48（47〜48） |
| cdkd deploy・zip | 初回作成 | 3 | 2 | 240（226〜254） | 189（189〜190） | - |
| cdkd deploy・zip | 画面 | 3 | 3 | 49（48〜52） | 17（17〜17） | 49（49〜52） |
| cdkd deploy・zip | API | 3 | 3 | 60（58〜64） | 28（27〜28） | 61（59〜65） |
| cdkd deploy・zip | エージェント | 3 | 3 | 74（73〜74） | 10（10〜11） | 93（93〜95） |
| cdkd deploy・zip | 削除 | 2 | 1 | 399 | - | - |
| cdkd deploy --no-wait・zip | 画面 | 3 | 3 | 50（50〜51） | 17（17〜17） | 51（51〜52） |
| cdkd deploy --no-wait・zip | API | 3 | 3 | 58（55〜60） | 26（25〜26） | 59（57〜61） |
| cdkd deploy --no-wait・zip | エージェント | 3 | 3 | 71（68〜81） | 10（9〜11） | 89（88〜103） |
| cdkd deploy・CodeBuild | エージェント | 3 | 3 | 133（131〜134） | 83（83〜85） | 143（142〜143） |
| cdkd deploy・CodeBuild | エージェントのデプロイ形式の切り替え | 1 | 1 | 157 | 117 | - |
| （参考）hotswap・Secretの差分が残っていた回 | 画面 | 3 | 3 | 80（74〜1053） | 47（43〜1019） | 81（75〜1053） |
| （参考）hotswap・Secretの差分が残っていた回 | API | 1 | 1 | 109 | 71 | 110 |

値は中央値（最小〜最大）。失敗した回は時間の集計から除く。
Amplifyはジョブの開始から終了まで（Hosting）と ampx sandbox --once の実行時間（sandbox）。それ以外はビルドからコマンドが終わるまで。
Amplify Hostingの「変更なしの再ビルド」は第4章の公開アプリ、それ以外のHostingの行は計測用に作ったアプリで測った。
「画面から直接呼ぶ」と付いた行のほかは、HonoのAPIを挟む形（AGENT_ACCESS=api）で測った。

補足の記録:
- cdkd deploy・zip・初回作成: 上の初回作成のあと、CloudFront経由の /api/health が200を返すまで（コマンド終了から約83秒）（反映まで 309秒）
- cdk deploy --hotswap-fallback・zip・API: --hotswap-fallback はコード置き場のS3の中身だけを差し替えて成功扱いになり、Web FunctionのRevisionは作り直されない。コマンドは28秒で終わったが、/api/health は古い版（v15）のまま。コードはv16
- cdkd deploy・zip・削除: 上の削除は実行中にこちらのネットワークでS3の名前解決が失敗し、状態の保存とカスタムリソースの削除が通らなかった。AWS上のリソースはすべて消えていたので記録を cdkd state orphan で削除。時間は計測から外す
- cdkd deploy・zip・初回作成: 1回目の失敗はGatewayのTargetの作成。ロールのIAMポリシーを付けた直後にTargetを作ると Resource Handler Internal Failure になる。TargetをMemoryの後に作るよう依存を足して2回目以降は成功
- cdk deploy・CodeBuild・エージェント: 失敗した1回はzipのRuntimeをそのままコンテナへ更新しようとした回。Agent artifact type cannot be updated で拒否されたので、置き方ごとにRuntimeを分けた
- （参考）hotswap・Secretの差分が残っていた回・画面: Express modeで作り直したスタックで、Secretの説明文の日本語がテンプレート上で「?」に化けて毎回差分になり、hotswapできずに通常のデプロイへ切り替わった回。2回目の1053秒は変更セットの作成で約16分待たされた外れ値（更新そのものは25秒）。説明文を英語にしてから測り直したのが hotswap-fallback の行
