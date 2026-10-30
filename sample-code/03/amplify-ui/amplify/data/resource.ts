import { type ClientSchema, a, defineData } from "@aws-amplify/backend"

/**
 * 第3章のRAGポータルから送信されるユーザーフィードバックを保存するモデル。
 * 第5章の評価ループで `sample-code/05/sync_feedback_from_dynamodb.py` から
 * Excel（feedback_workbook.xlsx）へ同期し、ナレッジ更新候補の抽出に使う。
 */
const schema = a.schema({
  Feedback: a
    .model({
      // クライアント側で生成した HistoryEntry.id（同じQ&Aの再評価を可能にする）
      historyEntryId: a.string().required(),
      // Q&A 本体（後で集計しやすいようにスナップショットとして保存）
      question: a.string().required(),
      agentAnswer: a.string().required(),
      // ユーザー評価（UP=役に立った / DOWN=改善希望）
      rating: a.enum(["UP", "DOWN"]),
      // 任意の補足コメント
      comment: a.string(),
      // 参照されたチャンクのURI（ナレッジ候補抽出時の手がかり）
      citations: a.string().array(),
      // 検索設定の手がかり（どの設定で出た回答へのフィードバックかを残す）
      searchPresetLabel: a.string(),
      filterDepartment: a.string(),
      latencyMs: a.integer(),
    })
    .authorization((allow) => [allow.owner()]),
})

export type Schema = ClientSchema<typeof schema>

export const data = defineData({
  schema,
  authorizationModes: {
    defaultAuthorizationMode: "userPool",
  },
})
