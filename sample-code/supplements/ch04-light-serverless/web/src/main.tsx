import React from "react"
import ReactDOM from "react-dom/client"
import { Amplify } from "aws-amplify"

import App from "./App"
import { loadConfig } from "./config"
import "@aws-amplify/ui-react/styles.css"
import "streamdown/styles.css"
import "./styles.css"
import "./workspace.css"

// CognitoのIDはデプロイ時にS3へ置いた /config.json から読む。
// 画面のビルドがスタックの出力を待たずに済む
const config = await loadConfig()

Amplify.configure({
  Auth: {
    Cognito: {
      userPoolId: config.userPoolId,
      userPoolClientId: config.userPoolClientId,
      loginWith: { email: true },
    },
  },
})

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
