import { createCodePlugin } from "@streamdown/code"
import { createMermaidPlugin } from "@streamdown/mermaid"

export const streamdownPlugins = {
  code: createCodePlugin({
    themes: ["vitesse-light", "vitesse-dark"],
  }),
  mermaid: createMermaidPlugin({
    config: {
      fontSize: 14,
    },
  }),
}
