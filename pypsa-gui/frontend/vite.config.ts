/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { authHtmlGatePlugin } from './vite.auth-gate'

export default defineConfig({
  plugins: [react(), tailwindcss(), authHtmlGatePlugin()],
  test: {
    // `node` (not jsdom) on purpose: this suite covers PURE helpers only —
    // no component rendering, so there is nothing to gain from a DOM and a
    // real cost in startup time. Add jsdom + @testing-library only when the
    // first component test lands.
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
  server: {
    // Pin to IPv4. On macOS, Vite's default `localhost` resolves to ::1, so the
    // dev server ends up on [::1]:5173 while uvicorn binds 127.0.0.1:8000 —
    // the browser still works via `localhost`, but anything addressing
    // 127.0.0.1:5173 (curl, smoke scripts, the e2e walkthroughs) gets a
    // connection refused. Binding explicitly keeps both services on IPv4 and
    // matches the proxy target below.
    // Cloud/agent previews override with `--host 0.0.0.0`.
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
