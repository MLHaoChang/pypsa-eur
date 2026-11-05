import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
const MODE = process.argv[2] || 'expert', CONV = process.argv[3] || 'false'
const SNAPS = Array.from({ length: 4 }, (_, i) => new Date(Date.UTC(2026, 0, 1, i)).toISOString().slice(0, 19))
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const p = await (await b.newContext({ viewport: { width: +(process.env.W||1600), height: 900 }, deviceScaleFactor: 2 })).newPage()
const proj = { id: 'p1', name: 'Demo', created_at: '2026-01-01T00:00:00', has_solver_config: true, bus_count: 0, snapshot_count: 4, objective: 1 }
const acpf = { available: true, converged_list: SNAPS.map((s, i) => ({ snapshot: s, ok: i === 0 ? (CONV === 'unknown' ? null : false) : true })) }
const R = {
  '/api/health': { auth_enabled: false }, '/api/projects/': [proj],
  '/api/network/snapshots': { snapshots: SNAPS, count: 4 },
  '/api/network/meta': { name: 'Demo', bus_count: 0, snapshot_count: 4 },
}
p.on('pageerror', e => console.log('PAGEERR', e.message.slice(0,200)))
await p.route(u => new URL(u).pathname.startsWith('/api/'), r => {
  const path = new URL(r.request().url()).pathname
  if (process.env.LOG) console.log(path)
  if (R[path]) return r.fulfill({ json: R[path] })
  if (path.includes('ac_pf') && path.endsWith('status')) return r.fulfill({ json: acpf })
  if (r.request().method() !== 'GET') return r.fulfill({ json: {} })
  if (path.startsWith('/api/network/')) return r.fulfill({ json: [] })
  r.fulfill({ status: 404, json: { detail: 'mock' } })
})
await p.goto('http://127.0.0.1:5173/projects'); await p.waitForTimeout(1500)
await p.goto('http://127.0.0.1:5173/app?project=p1'); await p.waitForTimeout(2500)
await p.evaluate(async (mode) => {
  const { useUIStore } = await import('/src/store/uiStore.ts')
  const s = useUIStore.getState()
  s.setUiMode?.(mode, { explicit: true })
  useUIStore.setState({ currentProject: 'Demo', resultsOverlayEnabled: true, resultSource: 'ac_pf', canvasView: 'blank', resultsSnapshotIdx: 0, activeSlidePanel: null })
}, MODE)
await p.waitForTimeout(2000)
if (MODE === 'guided') { await p.getByRole('button', { name: /^Close/ }).first().click(); await p.waitForTimeout(1500) }
await p.addStyleTag({ content: 'div:has(> div > [role=status]){display:none!important}' })
console.log(p.url())
await p.screenshot({ path: `shot-${MODE}-${CONV}${process.env.W?'-wide':''}.png` }); await b.close()
