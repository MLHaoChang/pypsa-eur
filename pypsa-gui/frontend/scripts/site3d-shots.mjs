// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Site 3D screenshot harness (reusable): the Site canvas of an enabled scene, Guided and Expert, with every
 * /api route mocked in the browser, so no backend or sign-in is needed. Invented demo data.
 *
 * Needs Vite on :5173. Vite's own auth gate asks the backend at :8000 for /api/health, so the script starts a
 * stub there (health only) unless something already answers.
 *
 * States: `legend` (the legend column against the Results pill), `readonly` (lock held by someone else), `readonly-item` (the same with an item chosen in Place),
 * `undo-reload` (the detach receipt comes back from the scene after a reload), `results-building` (SC-03: the Eye on, a
 * building selected and its group open, quarter-hourly labels), `results-unavailable` (SC-03: the run out of date, every
 * value unavailable in words). Add a state to STATES to extend.
 *
 * Usage: node scripts/site3d-shots.mjs [outDir]      env: BASE, ONLY=<state prefix>, DEBUG=1
 */
import fs from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'site-scene-generation', 'site3d-polish', 'evidence'))
const SCHEMAS = JSON.parse(await fs.readFile(path.join(frontend, 'src', 'test', 'fixtures', 'assetSchema.json'), 'utf8'))
const PROJECT = 'Site 3D shots'
const SITE = 'campus'

const bus = (name, x, y, carrier = 'AC') => ({ name, x, y, v_nom: 20, carrier, control: 'PQ' })
const BUSES = [bus('hv', 11.0, 48.0), bus('mv', 11.0004, 48.0003)]
const GENS = [{ name: 'pv', bus: 'mv', carrier: 'solar', p_nom: 5, p_nom_extendable: false }, { name: 'gas', bus: 'mv', carrier: 'gas', p_nom: 10, p_nom_extendable: false }]
const SUS = [{ name: 'bess', bus: 'mv', carrier: 'battery', p_nom: 8, max_hours: 2 }]
const LOADS = [{ name: 'hall', bus: 'mv', p_set: 4 }]
const placements = { 'StorageUnit:bess': { x: 20, y: 10, heading: 0 }, 'Generator:gas': { x: -30, y: 5, heading: 90 } }
const sitesDoc = () => ({ version: 1, asset_footprints: {}, sites: [{ id: SITE, name: 'Campus', buses: ['hv', 'mv'], origin: { lng: 11, lat: 48 },
  boundary: [[10.9985, 47.9988], [11.0015, 47.9988], [11.0015, 48.0012], [10.9985, 48.0012]], placements }] })
const scene = (lastReceipt) => ({ state: 'enabled', site_id: SITE, revision: 4, generation: 'g000004-aaaaaaaaaaaa',
  site: { placements: Object.fromEntries(Object.entries(placements).map(([k, v]) => [k, { ...v, on: 'ground' }])) },
  buildings: [], asset_footprints: {}, last_receipt: lastReceipt, unbound_keys: [], associations: [], targets: [],
  groups: [{ id: 'yard', label: 'Yard', members: ['StorageUnit:bess'] }] })
const DETACH = { proposal_id: 'p-detach-1', revision: 4, changed: ['placements', 'groups'], detached: { 'Generator:pv': 'uid-pv', 'Load:hall': 'uid-hall' } }

// SC-03: two buildings, an ammonia group and the D07 oracle's hand values (d07/plan.md §3) on quarter-hourly labels.
const square = (cx, cy, s) => [[cx - s, cy - s], [cx + s, cy - s], [cx + s, cy + s], [cx - s, cy + s]]
const BUILDINGS = [
  { id: 'b1', footprint: square(-15, 25, 8), height_m: 9, height_evidence: 'greenfield', source: { namespace: 'manual', source_id: 'm1' } },
  { id: 'b2', footprint: square(15, 25, 8), height_m: 6, height_evidence: 'greenfield', source: { namespace: 'manual', source_id: 'm2' } },
]
const LABELS = ['2030-01-01T00:00:00', '2030-01-01T00:15:00', '2030-01-01T00:30:00', '2030-01-01T00:45:00']
const Qn = (value, unit) => ({ value, available: true, reason: null, unit })
const NA = (reason, unit = null) => ({ value: null, available: false, reason, unit })
const sceneResults = (snapshot, current) => {
  const v = (x, unit) => (current ? Qn(x, unit) : NA('results_not_current', unit))
  const port = (port, bus, carrier, role, rate, extra = {}) => ({ port, bus, carrier, role, reversed: false, sign: 'withdrawal_positive', rate, ...extra })
  return {
    project: PROJECT, site_id: SITE, scene_revision: 4, run: { digest: 'd', current, reason: current ? null : 'results_not_current' },
    snapshot: { index: LABELS.indexOf(snapshot), label: snapshot, period: null, weight_generators: Qn(0.25, 'h'), period_years: Qn(1, 'a') },
    window: null,
    features: {
      b1: { targets: [{ class: 'Link', uid: 'u-el', name: 'electrolyser', allocation: null, ports: [
        port('p0', 'elec', 'AC', 'input', v(10, 'MW')), port('p1', 'h2', 'H2', 'output', v(-5, 'MW')), port('p2', 'heat', 'heat', 'output', v(-1, 'MW'))] }] },
      b2: { targets: [
        { class: 'Link', uid: 'u-hb', name: 'haber', allocation: null, ports: [
          port('p0', 'h2', 'H2', 'input', v(5, 'MW')), port('p1', 'n2', 'N2', 'input', v(1, 't/h')), port('p2', 'nh3', 'NH3', 'output', v(-4, 'MW'), { rate_t: v(-0.7692, 't/h') })] },
        { class: 'Load', uid: 'u-hall', name: 'hall', allocation: { basis: 'explicit', weight: 0.4 }, ports: [
          port('p', 'mv', 'AC', 'input', v(4, 'MW'), { allocated: v(1.6, 'MW') })] },
      ] },
    },
    groups: { plant: {
      boundary: [{ bus: 'elec', carrier: 'AC', net_import: v(10.25, 'MW') }, { bus: 'heat', carrier: 'heat', net_import: v(-1, 'MW') },
        { bus: 'n2', carrier: 'N2', net_import: v(1, 't/h') }, { bus: 'nh3', carrier: 'NH3', net_import: v(-4, 'MW') }],
      internal_buses: ['h2'],
      totals: [{ carrier: 'AC', net_import: v(10.25, 'MW') }, { carrier: 'heat', net_import: v(-1, 'MW') },
        { carrier: 'N2', net_import: v(1, 't/h') }, { carrier: 'NH3', net_import: v(-4, 'MW') }],
      total: NA(current ? 'mixed_units' : 'results_not_current'), unavailable_member: null,
    } },
  }
}

const STATES = {
  legend: { lastReceipt: null, readOnly: false },
  readonly: { lastReceipt: null, readOnly: true },
  'readonly-item': { lastReceipt: null, readOnly: true, pick: true },
  'undo-reload': { lastReceipt: DETACH, readOnly: false },
  'results-building': { lastReceipt: null, readOnly: false, results: { current: true } },
  'results-unavailable': { lastReceipt: null, readOnly: false, results: { current: false } },
}

async function ensureStub() {
  const up = await fetch('http://127.0.0.1:8000/api/health').then(r => r.ok).catch(() => false)
  if (up) return null
  const s = http.createServer((q, r) => { r.setHeader('content-type', 'application/json'); r.end(JSON.stringify({ status: 'ok', auth_enabled: false })) })
  await new Promise(res => s.listen(8000, '127.0.0.1', res))
  return s
}

await fs.mkdir(OUT, { recursive: true })
const stub = await ensureStub()
const browser = await chromium.launch()
const problems = [], written = []
try {
  for (const mode of ['guided', 'expert']) for (const [key, st] of Object.entries(STATES)) {
    if (process.env.ONLY && !key.startsWith(process.env.ONLY)) continue
    const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, reducedMotion: 'reduce' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ mode, project }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:assistant-dock', 'closed')
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
    }, { mode, project: PROJECT })
    const page = await ctx.newPage()
    page.setDefaultTimeout(15000)
    page.on('pageerror', e => problems.push(`${mode}/${key}: page error: ${(e.stack||e.message).split('\n').slice(0,4).join(' / ')}`))
    const unmocked = new Set()
    await page.route(u => u.pathname.startsWith('/api/'), r => {
      const p = new URL(r.request().url()).pathname, m = r.request().method()
      const ok = json => r.fulfill({ json })
      // The assistant and dictation are out of scope here: fail them the way an offline service does.
      if (p.startsWith('/api/chat/') || p.startsWith('/api/dictation/')) return r.abort()
      if (/\/flow$/.test(p)) return ok({ sections: [], run_request: { optimisation: true, study: false, loadflow: false }, notes: [], deferred: false, deferred_reason: null,
        scene: { entry: 'available', anchor_moved: [], actions: ['generate', 'assign_native'].map(action => ({ action, available: true, blocker: null, required_inputs: [], computes: '' })) } })
      if (p === '/api/simulation/queue') return ok({ jobs: [], running: [], paused: false })
      const sch = p.match(/^\/api\/network\/asset_schema\/([^/]+)$/)
      if (sch) return SCHEMAS[sch[1]] ? ok(SCHEMAS[sch[1]]) : r.fulfill({ status: 404, json: { detail: 'none' } })
      if (p === '/api/health') return ok({ status: 'ok', auth_enabled: false })
      if (p === '/api/projects/' || p === '/api/projects') return ok([{ name: PROJECT, bus_count: 2, snapshot_count: 24, has_results: false, created_at: '2026-01-01T00:00:00Z', is_root: true, parent: null }])
      if (p.endsWith(`/sites/${SITE}/scene`)) return ok(st.results
        ? { ...scene(st.lastReceipt), buildings: BUILDINGS, groups: [{ id: 'plant', label: 'Ammonia plant', members: ['b1', 'b2'] }] }
        : scene(st.lastReceipt))
      if (st.results && p.endsWith(`/sites/${SITE}/scene/results`)) return ok(sceneResults(new URL(r.request().url()).searchParams.get('snapshot'), st.results.current))
      // A solved run, so the Results pill is enabled; out of date in the unavailable state.
      if (st.results && p === '/api/simulation/status') return ok({ running: false, status: 'completed', condition: 'optimal', objective: 1234.5,
        solve_time: 2.5, dispatch: st.results.current ? 'fresh' : 'stale' })
      // The placed objects' own series are out of scope for these shots: none, as for a run that wrote none.
      if (st.results && p.startsWith('/api/results/')) return r.fulfill({ status: 200, contentType: 'application/json', body: 'null' })
      if (st.results && p === '/api/network/snapshots') return ok({ count: LABELS.length, snapshots: LABELS, weightings: [] })
      if (p.endsWith('/sites') && m === 'GET') return ok(sitesDoc())
      if (p.endsWith('/context')) return r.fulfill({ status: 404, json: { detail: 'none' } })
      const table = { '/api/network/buses': BUSES, '/api/network/generators': GENS, '/api/network/storage_units': SUS, '/api/network/loads': LOADS }
      if (table[p]) return ok(table[p])
      if (m === 'GET' && /\/api\/network\/[a-z_]+$/.test(p)) return ok([])
      unmocked.add(`${m} ${p}`)
      return ok(m === 'GET' && /(s|list|history|pending|workflows|profiles)$/.test(p) ? [] : {})
    })
    await page.goto(`${BASE}/`)
    await page.waitForLoadState('networkidle')
    const resume = page.getByRole('link', { name: /Resume project/ })
    if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
    await page.evaluate(async ({ readOnly }) => {
      const { useUIStore } = await import('/src/store/uiStore.ts')
      const ui = useUIStore.getState()
      ui.setCanvasView('site')
      if (readOnly) ui.setLockState({ readOnly: true, holderEmail: 'other@example.com', reason: 'locked-by-user' })
    }, { readOnly: st.readOnly && !st.pick })
    if (key === 'undo-reload') { await page.reload(); await page.waitForLoadState('networkidle')
      await page.evaluate(async () => { (await import('/src/store/uiStore.ts')).useUIStore.getState().setCanvasView('site') }) }
    // The mock backend has no assistant or map-context service, so their error toasts are not part of the shot.
    await page.addStyleTag({ content: 'div[style*="z-index: 9999"] { display: none !important }' })
    if (st.results) {
      await page.evaluate(async () => {
        const ui = (await import('/src/store/uiStore.ts')).useUIStore.getState()
        ui.setResultsOverlay(true)
        ui.setSitePartSelection({ kind: 'building', id: 'b2' })
      })
      await page.getByTestId('scene-result-group-plant').click()
    } else if (key !== 'legend') await page.getByRole('tab', { name: 'Model', exact: true }).click().catch(() => page.getByText('Model', { exact: true }).first().click())
    await page.waitForTimeout(2000)
    if (st.pick) {   // the select is disabled under a lock, so the item is chosen first and the lock arrives after
      await page.getByTestId('place-item').selectOption({ index: 1 })
      await page.evaluate(async () => { (await import('/src/store/uiStore.ts')).useUIStore.getState().setLockState({ readOnly: true, holderEmail: 'other@example.com', reason: 'locked-by-user' }) })
      await page.waitForTimeout(500)
    }
    const file = path.join(OUT, `${mode}-${key}.png`)
    await page.screenshot({ path: file }); written.push(path.basename(file))
    if (process.env.DEBUG) console.log(`${mode}/${key} unmocked:`, [...unmocked].join(' | '))
    await ctx.close()
  }
} finally { await browser.close(); stub?.close() }
console.log(`wrote ${written.length}: ${OUT}`)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
