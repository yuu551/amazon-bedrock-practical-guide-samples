import React from "react"
import ReactDOM from "react-dom/client"
import { Amplify } from "aws-amplify"

import App from "./App"
import outputs from "../amplify_outputs.json"
import "@aws-amplify/ui-react/styles.css"
import "streamdown/styles.css"
import "./styles.css"
import "./workspace.css"

Amplify.configure(outputs)

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
