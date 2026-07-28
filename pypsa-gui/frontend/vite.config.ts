/// <reference types="vitest" />
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { authHtmlGatePlugin } from './vite.auth-gate'

const rootDir = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  plugins: [react(), tailwindcss(), authHtmlGatePlugin()],
  appType: 'mpa',
  build: {
    rollupOptions: {
      input: {
        login: path.resolve(rootDir, 'index.html'),
        spa: path.resolve(rootDir, 'spa.html'),
      },
    },
  },
  test: {
    // jsdom (not `node`): component tests render React and query the DOM.
    // The suite was `node`-only while it covered pure helpers exclusively;
    // the first component test (Dialog) is what changed that.
    environment: 'jsdom',
    globals: false,
    include: [
      'src/**/*.test.ts',
      'src/**/*.test.tsx',
      'vite.auth-gate.test.ts',
      'brand.theme.test.ts',
    ],
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
