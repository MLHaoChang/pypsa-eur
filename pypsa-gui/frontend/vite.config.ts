import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Pin to IPv4. On macOS, Vite's default `localhost` resolves to ::1, so the
    // dev server ends up on [::1]:5173 while uvicorn binds 127.0.0.1:8000 —
    // the browser still works via `localhost`, but anything addressing
    // 127.0.0.1:5173 (curl, smoke scripts, the e2e walkthroughs) gets a
    // connection refused. Binding explicitly keeps both services on IPv4 and
    // matches the proxy target below.
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
