// Re-apply a project's commercial config after its snapshots changed (so preflight no longer says commercial.binding_invalid), then queue a solve.
import { createRequire } from 'module'
const { chromium } = createRequire('/home/user/pypsa-eur/.scratch/ui-modern/shots-02/tools/x.mjs')('playwright')
const [state, project] = process.argv.slice(2)
const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, storageState: state })
const p = await ctx.newPage()
await p.goto('http://127.0.0.1:5173/'); await p.waitForTimeout(2500)
await p.locator('article').filter({ hasText: new RegExp(`${project}(?!\\w)`) }).first().click(); await p.waitForTimeout(4500)
const r = await p.evaluate(async project => {
  const csrf = document.cookie.split('; ').find(c => c.startsWith('pypsa_gui_csrf='))?.split('=')[1]
  const h = { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }
  const cfg = await (await fetch('/api/simulation/solver_config', { credentials: 'include' })).json()
  const c = { ...cfg.commercial }; delete c.value_flows
  const put = await fetch('/api/simulation/solver_config', { method: 'PUT', credentials: 'include', headers: h, body: JSON.stringify({ commercial: c }) })
  const pf = await fetch('/api/simulation/preflight', { method: 'POST', credentials: 'include', headers: h, body: JSON.stringify({ project_id: project }) })
  return [put.status, (await put.text()).slice(0, 300), (await pf.text()).slice(0, 500)]
}, project)
console.log(JSON.stringify(r)); await b.close()
