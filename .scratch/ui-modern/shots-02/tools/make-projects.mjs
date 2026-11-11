import { createRequire } from 'module'
const { chromium } = createRequire(import.meta.url)('playwright')
const b = await chromium.launch()
const ctx = await b.newContext({ storageState: process.argv[2] })
const p = await ctx.newPage()
await p.goto((process.env.BASE_URL || 'http://127.0.0.1:5173') + '/')
const r = await p.evaluate(async (pairs) => {
  const csrf = document.cookie.split('; ').find(c => c.startsWith('pypsa_gui_csrf='))?.split('=')[1]
  const h = { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }
  const out = []
  for (const [tpl, name] of pairs) {
    const a = await fetch(`/api/projects/from_template/${tpl}?name=${name}`, { method: 'POST', headers: h, credentials: 'include' })
    const t = await a.text()
    out.push([tpl, a.status, (t.match(/"snapshots":\d+/) || [t.slice(0, 120)])[0]])
    if (a.status === 200) { const q = await fetch('/api/simulation/queue', { method: 'POST', headers: h, credentials: 'include', body: JSON.stringify({ project_id: name }) }); out.push(q.status) }
  }
  return out
}, process.argv.slice(3).map(s => s.split('=')))
console.log(JSON.stringify(r))
await b.close()
