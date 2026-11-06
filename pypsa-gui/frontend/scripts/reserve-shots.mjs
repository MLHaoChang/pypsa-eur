// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * DC grid readiness 06 screenshot harness: reserve products on the DC backup storage, Guided and Expert.
 * Every /api route is mocked in the browser (as site3d-shots.mjs), so no backend or sign-in is needed; the
 * reserve payload follows `GET /api/results/reserves` (backend/services/results/reserves.py). Invented
 * numbers and generic product names only.
 *
 * Needs Vite on :5173. Vite's auth gate asks :8000 for /api/health, so the script starts a stub there
 * (health only) unless something already answers.
 *
 * States: `a-up-down` (Results › Storage cycling with fcr, afrr_up and afrr_down on two strings),
 * `b-not-solved` (no current solve: "not solved", never 0), `c-no-reserves` (204: the view unchanged),
 * `d-dc-line` (the DC backup section's reserve line) and `d-dc-link` / `d-dc-back` (after its
 * "Storage view ›" link, then Back).
 *
 * Usage: node scripts/reserve-shots.mjs [outDir]      env: BASE, ONLY=<state prefix>, DEBUG=1
 */
import fs from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'datacenter-grid-readiness', 'shots', 'dc06'))
const SCHEMAS = JSON.parse(await fs.readFile(path.join(frontend, 'src', 'test', 'fixtures', 'assetSchema.json'), 'utf8'))
const PROJECT = 'Reserve shots'

// One week, hourly.
const H = 168
const SNAPS = Array.from({ length: H }, (_, i) => new Date(Date.UTC(2030, 0, 7, i)).toISOString().slice(0, 19))
const STRINGS = ['bess_1', 'bess_2']
const SUS = STRINGS.map(name => ({ name, bus: 'dc', carrier: 'battery', p_nom: 5, p_nom_opt: 5, max_hours: 4,
  p_nom_extendable: false, efficiency_store: 0.95, efficiency_dispatch: 0.95 }))
const E_CAP = 20, FLOOR = 8
const wave = (i, k, a, b) => a + b * (0.5 + 0.5 * Math.sin((i + k) * 2 * Math.PI / 24))
const soc = k => SNAPS.map((_, i) => +(FLOOR + 2 + wave(i, k, 0, 8)).toFixed(2))
const dispatch = { index: SNAPS, columns: STRINGS, data: SNAPS.map((_, i) => STRINGS.map((_, k) => +(2 * Math.sin((i + 3 * k) * 2 * Math.PI / 24)).toFixed(2))) }

const PRODUCTS = [
  { id: 'fcr', kind: 'fast_symmetric', direction: 'symmetric', sustain_h: 0.5, counterparty: 'tso', price_source: 'user' },
  { id: 'afrr_up', kind: 'secondary', direction: 'up', sustain_h: 1, counterparty: 'tso', price_source: 'demo' },
  { id: 'afrr_down', kind: 'secondary', direction: 'down', sustain_h: 1, counterparty: 'tso', price_source: 'demo' },
]
const NOT_MODELLED = 'activation energy and its price, cycling wear and non-delivery penalties are not modelled: capacity payments only'
const line = (id, amount, flags = []) => ({ period: null, contract_id: `reserve:${id}`, payer: 'tso', payee: 'site',
  value_stream: 'reserve_capacity', quantity_mwh: null, amount,
  flags: [...(id === 'fcr' ? [] : ['activation_not_valued']), ...flags] })

function solved() {
  return {
    products: PRODUCTS, snapshots: SNAPS,
    storages: STRINGS.map((name, k) => ({
      name, string: true, floor_mwh: FLOOR, e_cap_mwh: E_CAP, soc_mwh: soc(3 * k),
      committed: {
        fcr: SNAPS.map(() => 1),
        afrr_up: SNAPS.map((_, i) => +wave(i, 3 * k, 0, 2).toFixed(2)),
        afrr_down: SNAPS.map((_, i) => +wave(i, 3 * k + 12, 0, 1.5).toFixed(2)),
      },
    })),
    lines: [line('fcr', 4200), line('afrr_up', 2650), line('afrr_down', 1380)],
    flags: [], headroom_h_per_string: 1, currency: 'EUR', money_year: 2030, not_modelled: NOT_MODELLED,
  }
}
function notSolved() {
  const p = solved()
  return { ...p, storages: p.storages.map(s => ({ ...s, soc_mwh: null, committed: null })),
    lines: p.lines.map(l => ({ ...l, amount: null, flags: [...l.flags, 'reserve_not_solved'] })), flags: ['reserve_not_solved'] }
}

const STATES = {
  'a-up-down': { reserves: solved },
  'b-not-solved': { reserves: notSolved },
  'c-no-reserves': { reserves: null },
  'd-dc': { reserves: solved, dc: true },
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
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 2, reducedMotion: 'reduce' })
    await ctx.route(u => ['http:', 'https:'].includes(u.protocol) && u.hostname !== '127.0.0.1', r => r.abort())
    await ctx.addInitScript(({ mode, project }) => {
      localStorage.setItem('network-diagram:current-project', project)
      localStorage.setItem('network-diagram:assistant-dock', 'closed')
      localStorage.setItem('network-diagram:ui-mode', mode)
      localStorage.setItem('network-diagram:ui-mode-explicit', '1')
      localStorage.setItem('network-diagram:theme', 'light')
      localStorage.setItem('network-diagram:theme-schema', '2')
    }, { mode, project: PROJECT })
    const page = await ctx.newPage()
    page.setDefaultTimeout(15000)
    page.on('pageerror', e => problems.push(`${mode}/${key}: page error: ${(e.stack || e.message).split('\n').slice(0, 4).join(' / ')}`))
    if (process.env.DEBUG) page.on('console', m => { if (m.type() === 'error') console.log(`${mode}/${key} console: ${m.text().slice(0, 600)}`) })
    const unmocked = new Set()
    await page.route(u => u.pathname.startsWith('/api/'), r => {
      const p = new URL(r.request().url()).pathname, m = r.request().method()
      const ok = json => r.fulfill({ json })
      if (p.startsWith('/api/chat/') || p.startsWith('/api/dictation/')) return r.abort()
      if (p === '/api/health') return ok({ status: 'ok', auth_enabled: false })
      if (p === '/api/results/reserves') return st.reserves ? ok(st.reserves()) : r.fulfill({ status: 204, body: '' })
      if (p === '/api/results/storage_dispatch') return ok(dispatch)
      if (p === '/api/network/storage_units') return ok(SUS)
      if (p === '/api/network/vintage_results') return ok({ results: {} })
      if (p === '/api/network/snapshots') return ok({ count: H, snapshots: SNAPS, weightings: [], ts_start: SNAPS[0], ts_end: SNAPS[H - 1], can_sample_weeks: false })
      if (p === '/api/network/investment_periods') return ok({ periods: [], weightings: [] })
      if (p === '/api/simulation/status') return ok({ running: false, status: 'completed', condition: 'optimal', objective: 1234.5, solve_time: 2.5, dispatch: 'fresh' })
      if (p === '/api/simulation/queue') return ok({ jobs: [], running: [], paused: false })
      if (p.endsWith('/eh_template')) return ok({ id: 'eh_datacenter', dc_load: { spec: { backup: { mode: 'bess' } } } })
      if (/^\/api\/campus-electrical\/[^/]+$/.test(p)) return ok({ campus_yaml: null, skipped: [], profiles: {}, stale: false,
        hub_cost: null, hub_cost_reason: null, results: null, settings: null })
      // No stored scope: every card counts as in scope (useInScope before a read).
      if (p.endsWith('/flow/scope')) return r.fulfill({ status: 404, json: { detail: 'none' } })
      if (p.endsWith('/sites') && m === 'GET') return ok({ version: 1, asset_footprints: {}, sites: [] })
      // Neighbouring sections of the campus study panel: nothing run yet.
      if (p.endsWith('/intent-comparison') || p.endsWith('/island') || p.endsWith('/reliability_goal')) return r.fulfill({ status: 404, json: { detail: 'none' } })
      if (/\/flow$/.test(p)) return ok({ sections: [], run_request: { optimisation: true, study: false, loadflow: false }, notes: [], deferred: false, deferred_reason: null })
      const sch = p.match(/^\/api\/network\/asset_schema\/([^/]+)$/)
      if (sch) return SCHEMAS[sch[1]] ? ok(SCHEMAS[sch[1]]) : r.fulfill({ status: 404, json: { detail: 'none' } })
      if (p === '/api/projects/' || p === '/api/projects') return ok([{ name: PROJECT, bus_count: 2, snapshot_count: H, has_results: true, created_at: '2026-01-01T00:00:00Z', is_root: true, parent: null }])
      if (p.endsWith('/context')) return r.fulfill({ status: 404, json: { detail: 'none' } })
      if (m === 'GET' && p.startsWith('/api/results/')) return r.fulfill({ status: 204, body: '' })
      if (m === 'GET' && /\/api\/network\/[a-z_]+$/.test(p)) return ok([])
      unmocked.add(`${m} ${p}`)
      return ok(m === 'GET' && /(s|list|history|pending|workflows|profiles)$/.test(p) ? [] : {})
    })
    const shoot = async (name, locator) => {
      const file = path.join(OUT, `dc06-${mode}-${name}.png`)
      if (!locator) { await page.screenshot({ path: file }); written.push(path.basename(file)); return }
      // A section taller than the window would be clipped by its scroll container: grow the window for the crop,
      // and hide the fixed overlays outside it (chat dock, Back pill), as dips-restart-shots.mjs does.
      const view = page.viewportSize()
      const tall = (await locator.boundingBox())?.height ?? 0
      await page.setViewportSize({ ...view, height: Math.max(view.height, Math.ceil(tall) + 600) }); await page.waitForTimeout(600)
      await locator.scrollIntoViewIfNeeded()
      await locator.evaluate(el => {
        window.__shotHidden = []
        for (const n of document.body.querySelectorAll('*')) {
          if (el.contains(n) || n.contains(el)) continue
          if ((getComputedStyle(n).position === 'fixed' || n.matches('[data-testid="companion-launcher"]')) && n.style.visibility !== 'hidden') { n.style.visibility = 'hidden'; window.__shotHidden.push(n) }
        }
      })
      await locator.screenshot({ path: file })
      await page.evaluate(() => { for (const n of window.__shotHidden ?? []) n.style.visibility = '' })
      if (page.viewportSize().height !== view.height) await page.setViewportSize(view)
      written.push(path.basename(file))
    }
    try {
      await page.goto(`${BASE}/`)
      await page.waitForLoadState('networkidle')
      const resume = page.getByRole('link', { name: /Resume project/ })
      if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
      await page.addStyleTag({ content: 'div[style*="z-index: 9999"] { display: none !important }' })
      if (!st.dc) {
        await page.evaluate(async () => { const { showResultsTab } = await import('/src/layout/flowNavigation.ts'); showResultsTab('storage') })
        await page.getByText('Per-unit detail', { exact: true }).first().waitFor()
        await page.waitForTimeout(1500)
        await shoot(key)
        const section = page.getByTestId('reserve-section')
        if (await section.count()) await shoot(`${key}-section`, section.locator('xpath=ancestor::section[1]'))
      } else {
        await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('campusStudy') })
        const lineEl = page.getByTestId('dc-backup-reserves')
        await lineEl.waitFor(); await page.waitForTimeout(800)
        await shoot(`${key}-line`, page.getByTestId('dc-backup'))
        await page.getByTestId('dc-backup-reserves-open').click()
        await page.getByTestId('reserve-section').waitFor(); await page.waitForTimeout(1200)
        await shoot(`${key}-link`)
        // The flow return pill (Guided's Results header has its own "Back to Results").
        await page.getByRole('button', { name: 'Back to Scenarios & optimisation' }).click()
        await lineEl.waitFor(); await page.waitForTimeout(800)
        await shoot(`${key}-back`)
      }
    } catch (e) {
      problems.push(`${mode}/${key}: ${e.message.split('\n')[0]}`)
      await page.screenshot({ path: path.join(OUT, `failed-${mode}-${key}.png`) }).catch(() => {})
    }
    if (process.env.DEBUG) console.log(`${mode}/${key} unmocked:`, [...unmocked].join(' | '))
    await ctx.close()
  }
} finally { await browser.close(); stub?.close() }
console.log(`wrote ${written.length}: ${OUT}\n${written.join(', ')}`)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
