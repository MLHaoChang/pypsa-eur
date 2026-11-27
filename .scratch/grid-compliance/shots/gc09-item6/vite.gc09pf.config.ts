// Vite for the GC-09 item 6 harness: :5182, the app's own plugins less the auth gate (the harness page has no
// sign-in; every /api request is answered by Playwright's page.route). run.sh copies this and the harness files
// into pypsa-gui/frontend (untracked) for the run and removes them after.
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  appType: 'mpa',
  server: { port: 5182, strictPort: true, host: '127.0.0.1' },
})
