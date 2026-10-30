import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from "path"

// Where the app is served from. Root ("/") in development and on Databricks Apps;
// "/peoplemodelling/" when IIS serves it as an application under Default Web Site on
// gencodev.tnb.com.my. Read from the environment rather than hardcoded, so the same
// source builds for both and a change of path is a build flag, not an edit:
//
//   APP_BASE_PATH=/peoplemodelling/ npm run build
//
// Vite exposes this to the app as import.meta.env.BASE_URL, which is what the router
// basename and the axios base URL are derived from -- see src/main.tsx. Must end in a
// slash; Vite warns and corrects otherwise.
const rawBase = process.env.APP_BASE_PATH?.trim() || "/"
const base = rawBase.endsWith("/") ? rawBase : `${rawBase}/`

// The backend for `npm run dev`. The app calls relative /api/* paths so that one origin
// serves both in production; the dev server is a second origin, so without this proxy
// every API call in development 404s against Vite itself.
const devApiTarget = process.env.DEV_API_TARGET?.trim() || "http://127.0.0.1:8000"

// https://vitejs.dev/config/
export default defineConfig({
  base,
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      "/api": { target: devApiTarget, changeOrigin: true },
    },
  },
})
