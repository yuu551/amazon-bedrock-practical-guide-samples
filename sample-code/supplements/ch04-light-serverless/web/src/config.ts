export type AppConfig = {
  region: string
  userPoolId: string
  userPoolClientId: string
  // 画面からRuntimeを直接呼ぶときだけ入る。ないときは /api/chat（Hono）へ送る
  agentRuntimeArn?: string
}

const E2E_CONFIG: AppConfig = {
  region: "us-east-1",
  userPoolId: "us-east-1_e2e",
  userPoolClientId: "e2e-client",
}

let configPromise: Promise<AppConfig> | undefined

// デプロイ時にS3へ置いた /config.json を1回だけ読み、以降は同じ値を返す
export function loadConfig(): Promise<AppConfig> {
  if (import.meta.env.MODE === "e2e") return Promise.resolve(E2E_CONFIG)

  configPromise ??= fetch("/config.json", { cache: "no-store" }).then(async (res) => {
    if (!res.ok) {
      throw new Error(`/config.json を読み込めませんでした（${res.status}）`)
    }
    return (await res.json()) as AppConfig
  })
  return configPromise
}
