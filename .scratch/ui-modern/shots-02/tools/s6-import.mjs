// Import a NetCDF network as a new project, switch AC power flow on (and drop the commercial config the
// working state still holds from the last open project: it fails validation on the new network), queue the solve.
//   node s6-import.mjs <state.json> <network.nc> <project name>      (env BASE_URL as the other tools)
import { createRequire } from 'module'
import fs from 'fs'
const { chromium } = createRequire(import.meta.url)('playwright')
const [state, file, name] = process.argv.slice(2)
const b = await chromium.launch()
const ctx = await b.newContext({ storageState: state })
const p = await ctx.newPage()
await p.goto((process.env.BASE_URL || 'http://127.0.0.1:5173') + '/')
const r = await p.evaluate(async ({ b64, name }) => {
  const csrf = document.cookie.split('; ').find(c => c.startsWith('pypsa_gui_csrf='))?.split('=')[1]
  const h = { 'X-CSRF-Token': csrf }, jh = { ...h, 'Content-Type': 'application/json' }
  const out = []
  const j = async (u, o) => { const r = await fetch(u, { credentials: 'include', ...o }); out.push([u, r.status, (await r.text()).slice(0, 160)]) }
  const fd = new FormData(); fd.append('file', new Blob([Uint8Array.from(atob(b64), c => c.charCodeAt(0))]), 'network.nc')
  await j('/api/io/import/netcdf', { method: 'POST', headers: h, body: fd })
  await j(`/api/projects/${name}?rebind=true`, { method: 'POST', headers: jh })
  await j('/api/simulation/solver_config', { method: 'PUT', headers: jh, body: JSON.stringify({ run_ac_pf_after_lopf: true, commercial: null }) })
  await j(`/api/projects/${name}?force=true`, { method: 'POST', headers: jh })
  await j('/api/simulation/queue', { method: 'POST', headers: jh, body: JSON.stringify({ project_id: name }) })
  return out
}, { b64: fs.readFileSync(file).toString('base64'), name })
console.log(JSON.stringify(r, null, 1))
await b.close()
