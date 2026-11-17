// Shots for homer-parity 02i (#1541): the slope hint in the generator form. Vite on :5173 and fake.mjs on :8000
// (../../ac-pf-banner-shots/fake.mjs); every /api route is mocked. node shoot-slope.mjs <expert|guided> <0.5|1.2>
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs'
import fs from 'fs'
const MODE = process.argv[2] || 'expert', SLOPE = process.argv[3] || '0.5'
const schemas = JSON.parse(fs.readFileSync('../../../pypsa-gui/frontend/src/test/fixtures/assetSchema.json', 'utf8'))
const SNAPS = Array.from({ length: 4 }, (_, i) => new Date(Date.UTC(2026, 0, 1, i)).toISOString().slice(0, 19))
const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const p = await (await b.newContext({ viewport: { width: 1600, height: 1100 }, deviceScaleFactor: 2 })).newPage()
const proj = { id: 'p1', name: 'Demo', created_at: '2026-01-01T00:00:00', has_solver_config: true, bus_count: 1, snapshot_count: 4, objective: 1 }
const gen = { name: 'diesel genset', bus: 'site', carrier: 'diesel', p_nom: 2, p_nom_extendable: false, marginal_cost: 0, efficiency: 0.4, fuel_curve_slope: +SLOPE, fuel_price: 30, committable: true, control: 'PQ' }
const R = {
  '/api/health': { auth_enabled: false }, '/api/projects/': [proj],
  '/api/network/snapshots': { snapshots: SNAPS, count: 4 }, '/api/network/meta': { name: 'Demo', bus_count: 1, snapshot_count: 4 },
  '/api/network/generators': [gen], '/api/network/buses': [{ name: 'site', v_nom: 20, carrier: 'AC', x: 0, y: 0 }],
}
p.on('console', m => { if (m.type()==='error') console.log('CONSOLE', m.text().slice(0,300)) })
p.on('pageerror', e => console.log('PAGEERR', e.message.slice(0, 200)))
await p.route(u => new URL(u).pathname.startsWith('/api/'), r => {
  const path = new URL(r.request().url()).pathname
  if (R[path]) return r.fulfill({ json: R[path] })
  let q = path.match(/^\/api\/network\/catalog\/(\w+)$/)
  if (q) return r.fulfill({ json: { component: q[1], attributes: [] } })
  q = path.match(/^\/api\/network\/asset_schema\/(\w+)\/units\/(.+)$/)
  if (q) return r.fulfill({ json: { component: q[1], name: q[2], units: {} } })
  q = path.match(/^\/api\/network\/asset_schema\/(\w+)\/provenance\/(.+)$/)
  if (q) return r.fulfill({ json: { component: q[1], name: q[2], fields: {} } })
  const m = path.match(/^\/api\/network\/asset_schema\/(\w+)$/)
  if (m && schemas[m[1]]) return r.fulfill({ json: schemas[m[1]] })
  if (r.request().method() !== 'GET') return r.fulfill({ json: {} })
  if (path.startsWith('/api/network/')) { console.log('LOGEMPTY', path); return r.fulfill({ json: [] }) } if (0) return r.fulfill({ json: [] })
  console.log('LOG404', r.request().method(), path); r.fulfill({ status: 404, json: { detail: 'mock' } })
})
await p.goto('http://127.0.0.1:5173/projects'); await p.waitForTimeout(1500)
await p.goto('http://127.0.0.1:5173/app?project=p1'); await p.waitForTimeout(2500)
await p.evaluate(async (mode) => {
  const { useUIStore } = await import('/src/store/uiStore.ts')
  const s = useUIStore.getState()
  s.setUiMode?.(mode, { explicit: true })
  useUIStore.setState({ currentProject: 'Demo', canvasView: 'blank' })
  s.setSelectedComponent({ type: 'Generator', name: 'diesel genset' })
}, MODE)
await p.waitForTimeout(3000)
await p.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().requestPropertiesEdit({ type: 'Generator', name: 'diesel genset' }) })
await p.waitForTimeout(1500)
await p.addStyleTag({ content: 'div:has(> div > [role=status]){display:none!important}' })
const eb = p.getByRole('button', { name: /Edit Generator/ }).or(p.getByTitle(/Edit Generator/)).first()
if (await eb.count()) { await eb.click(); await p.waitForTimeout(1500) } else console.log('no edit button')
console.log((await p.locator('button').allInnerTexts()).filter(t=>t.trim()).slice(0,60).join(' | '))
console.log('hint:', await p.getByTestId('asset-slope-hint').count())
const h = p.getByTestId('asset-slope-hint')
if (await h.count()) await h.scrollIntoViewIfNeeded(); else await p.getByText('Fuel curve slope', { exact: false }).first().scrollIntoViewIfNeeded().catch(() => {})
await p.screenshot({ path: `shot-slope-${MODE}-${SLOPE}.png` })
await b.close()
