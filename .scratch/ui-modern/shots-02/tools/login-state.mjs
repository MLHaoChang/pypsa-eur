import { createRequire } from 'module'
const { chromium } = createRequire(import.meta.url)('playwright')
import fs from 'fs'
const pw = fs.readFileSync(process.env.HOME + '/.local/share/PyPSA Studio/dev-admin-password', 'utf8').trim()
const name = process.argv[2]
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } })
const p = await ctx.newPage()
await p.goto((process.env.BASE_URL || 'http://127.0.0.1:5173') + '/')
const r = await p.evaluate(async ({ pw, name }) => {
  const j = async (u, o) => { const r = await fetch(u, { credentials: 'include', ...o }); return [r.status, (await r.text()).slice(0, 200)] }
  const out = []
  out.push(await j('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: 'admin@example.com', password: pw }) }))
  const csrf = document.cookie.split('; ').find(c => c.startsWith('pypsa_gui_csrf='))?.split('=')[1]
  const h = { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }
  out.push(await j(`/api/projects/from_template/eh_datacenter?name=${name}`, { method: 'POST', headers: h }))
  out.push(await j('/api/simulation/queue', { method: 'POST', headers: h, body: JSON.stringify({ project_id: name }) }))
  return out
}, { pw, name })
console.log(JSON.stringify(r))
await ctx.storageState({ path: process.argv[3] })
await b.close()
