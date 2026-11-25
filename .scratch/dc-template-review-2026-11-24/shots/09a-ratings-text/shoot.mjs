// DC 09a shot: handoff-table-expert-light, the Assumptions list in frame (copy of 09-equipment-schedule/shoot.mjs).
// schedule.json here is 09's with the two engine texts taken from the code (campus_study.CABLE_DESIGN_BASIS, campus_ratings k unit).
// Component harness: a temporary harness page (src/ paths pass the dev auth gate) under pypsa-gui/frontend is created, served by
// vite, and removed in `finally`. The schedule JSON is the real backend's services.equipment_schedule.build
// (generic bus names), default schedule.json beside this script (or argv[2]). Run: node shoot.mjs
import fs from 'node:fs'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const { chromium } = require('playwright')

const OUT = path.dirname(fileURLToPath(import.meta.url))
const FE = path.resolve(OUT, '../../../../pypsa-gui/frontend')
const sched = JSON.parse(fs.readFileSync(process.argv[2] ?? path.join(OUT, 'schedule.json'), 'utf8'))
const PORT = 5234
const html = path.join(FE, 'src/harness09a.tmp.html')
const tsx = path.join(FE, 'src/harness09a.tmp.tsx')

fs.writeFileSync(html, `<!doctype html><html lang="en"><head><meta charset="UTF-8"/><title>h</title>
<link rel="stylesheet" href="/brand.css" /></head><body><div id="root"></div>
<script type="module" src="/src/harness09a.tmp.tsx"></script></body></html>`)
fs.writeFileSync(tsx, `import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import { useUIStore } from './store/uiStore'
import EquipmentScheduleTab from './pages/results/EquipmentScheduleTab'
import SwitchgearRatingTable from './pages/SwitchgearRatingTable'
const q = new URLSearchParams(location.search)
document.documentElement.dataset.theme = q.get('theme') ?? 'light'
useUIStore.setState({ uiMode: (q.get('mode') ?? 'guided') as 'guided' | 'expert', currentProject: 'Demo' })
const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
const Short = () => (
  <div className="px-8 py-5 bg-surface text-text">
    <h2 className="text-[12px] font-semibold mb-1">Short circuit <span className="font-normal text-muted">IEC 60909, every installed unit energised, per period</span></h2>
    <SwitchgearRatingTable />
  </div>)
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={qc}>
    <div id="frame" style={{ width: q.get('view') === 'short' ? 1128 : 1200, display: 'flex', flexDirection: 'column' }} className="bg-bg text-text">
      {q.get('view') === 'short' ? <Short /> : <EquipmentScheduleTab />}
    </div>
  </QueryClientProvider>)
`)

let vite
try {
  vite = spawn(path.join(FE, 'node_modules/.bin/vite'), ['--port', String(PORT), '--host', '127.0.0.1', '--strictPort'], { cwd: FE, stdio: 'ignore' })
  const b = await chromium.launch()
  for (let i = 0; i < 60; i++) { try { if ((await fetch(`http://127.0.0.1:${PORT}/src/harness09a.tmp.html`)).ok) break } catch {} await new Promise(r => setTimeout(r, 1000)) }
  const shoot = async (view, mode, theme, name, stale, scrolled) => {
    const ctx = await b.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 })
    const p = await ctx.newPage()
    await p.route('**/api/campus-electrical/Demo/equipment-schedule', r => r.fulfill({ json: { ...sched, stale } }))
    p.on('console', m => m.type()==='error' && console.log('console', m.text().slice(0,200))); p.on('response', r => r.status()>=400 && console.log('http', r.status(), r.url())); p.on('pageerror', e => console.log('pageerror', e.message.slice(0,300)))
    await p.goto(`http://127.0.0.1:${PORT}/src/harness09a.tmp.html?view=${view}&mode=${mode}&theme=${theme}`)
    await p.waitForSelector(view === 'short' ? '[data-testid=switchgear-chosen-scroll]' : '[data-testid=schedule-assumptions]')
    if (scrolled) await p.locator('[data-testid=switchgear-chosen-scroll]').evaluate(e => { const s = [e, ...e.querySelectorAll('*')].find(x => x.scrollHeight > x.clientHeight + 1) ?? e; s.scrollTop = s.scrollHeight })
    await p.waitForTimeout(600)
    await p.locator('#frame').screenshot({ path: path.join(OUT, `${name}-${mode}-${theme}.png`) })
    await ctx.close()
  }
  await shoot('table', 'expert', 'light', 'handoff-table', false)
  await b.close()
} finally {
  vite?.kill()
  fs.rmSync(html, { force: true }); fs.rmSync(tsx, { force: true })
}
