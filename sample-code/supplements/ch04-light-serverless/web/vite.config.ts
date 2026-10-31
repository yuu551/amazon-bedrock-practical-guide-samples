import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

export default defineConfig({
  plugins: [react()],
  // main.tsx でトップレベルの await を使う
  build: { target: "es2022" },
})
