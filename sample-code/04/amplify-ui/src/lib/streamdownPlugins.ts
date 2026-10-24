import { createCodePlugin } from "@streamdown/code"

export const streamdownPlugins = {
  code: createCodePlugin({
    themes: ["vitesse-light", "vitesse-dark"],
  }),
}
