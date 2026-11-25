// Vite for the GC-08e harness: :5181, the app's own plugins less the auth gate (the harness page has no
// sign-in; every /api request is answered by Playwright's page.route). run.sh copies this, gc08e-harness.html
// and gc08e-harness.tsx into pypsa-gui/frontend (untracked) for the run and removes them after.
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  appType: 'mpa',
  server: { port: 5181, strictPort: true, host: '127.0.0.1' },
})
