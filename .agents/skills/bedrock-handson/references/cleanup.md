# 後片付け

作った環境の削除は、`ハンズオン後のリソース削除手順.md`の表に、章ごとの作成コマンドと削除コマンドがあります。読者が実行した作成コマンドを聞き、同じ行の削除コマンドを、そこに書かれたディレクトリで実行してもらいます。

## 削除の順番

後の章で作ったものから消すと、依存関係で止まりにくくなります。

1. 第6章のスタック（Amplify版、CDK版）
2. 第4章の画面のsandbox（AgentCore Runtime、Gateway、Secrets Managerのシークレットを含む）
3. 第3章の比較用のKnowledge Baseと、画面のsandbox
4. 第2章のKnowledge Base、S3バケット、画面のsandbox
5. 第1章のGuardrailと、weatherDemoをデプロイした場合は、その環境

第3章の比較用のKnowledge Baseは、第2章の資料を使うので、第2章より先に消します。第5章だけのAWSリソースはありません。

## 実行するとき

- 削除のコマンドは、対象を示して、読者の了承を得てから実行する
- weatherDemoの`agentcore remove all`は、手元の設定を空にするだけで、AWS上のものは消さない。続けて`agentcore deploy`を実行すると、空の設定に合わせてRuntimeとMemoryが削除される
- 削除が`DELETE_FAILED`で止まったら、同じコマンドをやり直す。それでも通らなければ、`troubleshooting.md`の「削除と費用」を見る

## 残りの確認

`scripts/find_leftovers.sh`を実行し、ハンズオンで作ったものが一覧に残っていないか、読者と一緒に確かめます。

- OpenSearch Serverlessのコレクション（料金への影響が最も大きい）
- Knowledge Base
- S3 Vectorsのベクトルバケット
- AgentCore RuntimeとGateway
- CloudFormationのスタック（`SupportAi`、`KbUpdateAgent`、`amplify-`、`AgentCore-`で始まるもの）
- S3バケット、IAMロール、Secrets Managerのシークレット、Guardrail（`bedrock-practical-guide-ch01`）

ほかの用途で作ったものも、一覧に含まれます。名前から判断できないものは削除せず、読者に確かめてもらいます。
