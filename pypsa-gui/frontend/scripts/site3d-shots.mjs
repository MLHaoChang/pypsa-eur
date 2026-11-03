// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Site 3D screenshot harness (reusable): the Site canvas of an enabled scene, Guided and Expert, with every
 * /api route mocked in the browser, so no backend or sign-in is needed. Invented demo data.
 *
 * Needs Vite on :5173. Vite's own auth gate asks the backend at :8000 for /api/health, so the script starts a
 * stub there (health only) unless something already answers.
 *
 * States: `legend` (the legend column against the Results pill), `readonly` (lock held by someone else),
 * `undo-reload` (the detach receipt comes back from the scene after a reload). Add a state to STATES to extend.
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

const STATES = {
  legend: { lastReceipt: null, readOnly: false },
  readonly: { lastReceipt: null, readOnly: true },
  'undo-reload': { lastReceipt: DETACH, readOnly: false },
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
      if (p.endsWith(`/sites/${SITE}/scene`)) return ok(scene(st.lastReceipt))
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
    }, { readOnly: st.readOnly })
    if (key === 'undo-reload') { await page.reload(); await page.waitForLoadState('networkidle')
      await page.evaluate(async () => { (await import('/src/store/uiStore.ts')).useUIStore.getState().setCanvasView('site') }) }
    // The mock backend has no assistant or map-context service, so their error toasts are not part of the shot.
    await page.addStyleTag({ content: 'div[style*="z-index: 9999"] { display: none !important }' })
    if (key !== 'legend') await page.getByRole('tab', { name: 'Model', exact: true }).click().catch(() => page.getByText('Model', { exact: true }).first().click())
    await page.waitForTimeout(2000)
    const file = path.join(OUT, `${mode}-${key}.png`)
    await page.screenshot({ path: file }); written.push(path.basename(file))
    if (process.env.DEBUG) console.log(`${mode}/${key} unmocked:`, [...unmocked].join(' | '))
    await ctx.close()
  }
} finally { await browser.close(); stub?.close() }
console.log(`wrote ${written.length}: ${OUT}`)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
