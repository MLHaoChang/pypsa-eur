// Vite for the GC-09 shots: :5180, proxying /api to the seeded backend on :8010.
// run.sh copies it into pypsa-gui/frontend (untracked) and removes it after:
//   npx vite --config vite.shots-8010.config.ts

import base from './vite.config'

const config = base as unknown as { server: Record<string, unknown> }
config.server = { ...config.server, port: 5180, strictPort: true, proxy: { '/api': 'http://127.0.0.1:8010' } }
export default config
