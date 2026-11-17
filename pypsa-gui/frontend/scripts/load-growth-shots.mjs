// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Homer-parity 04a screenshot harness: Model Horizon > Economics > Advanced, the "Load growth (% per year)"
 * row, Guided and Expert. Every /api route is mocked in the browser (as reserve-shots.mjs), so no backend or
 * sign-in is needed; solver_config, investment_periods and snapshots are route mocks (periods 2030/2035/2040).
 *
 * Needs Vite on :5173 (started here when nothing answers). Vite's auth gate asks :8000 for /api/health, so a
 * health-only stub is started there unless something already answers.
 *
 * States: rate-only, typed-override, validation-error, empty.
 * Usage: node scripts/load-growth-shots.mjs [outDir]      env: BASE, ONLY=<state prefix>, DEBUG=1
 */
import fs from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'homer-parity', 'shots-04a'))
const PROJECT = 'Load growth shots'
const PERIODS = [2030, 2035, 2040]

const baseCfg = over => ({
  solver_name: 'highs', mode: 'lopf', transmission_losses: false, multi_investment_periods: true, solver_options: {},
  extra_functionality_code: '', discount_rate: 0.07, inflation_rate: 0, default_lifetime: 25, co2_price: 0,
  co2_price_per_period: {}, voll: 0, investment_periods: PERIODS, sclopf: false, sclopf_include_all_lines: false,
  sclopf_include_all_transformers: false, sclopf_voltage_threshold_kv: 0, sclopf_extra_lines: [],
  sclopf_extra_transformers: [], sclopf_scope: 'horizon', run_ac_pf_after_lopf: false, ac_pf_slack_bus: '',
  ac_pf_x_tol: 1e-6, presolve_enabled: true, user_objective_scale: 1, auto_objective_scale: false,
  solve_strategy: 'full', rolling_horizon: 168, rolling_overlap: 24, lf_aggregate_future: false, lf_k_periods: 8,
  lf_period_length_h: 168, lf_cluster_method: 'hierarchical', lf_include_extreme: true, mip_gap: 0.01,
  mip_time_limit_s: 0, auto_discount_periods: false, ...over,
})
const RATE = { load_scalers_by_carrier: { electrical: { 2035: 1.104081, 2040: 1.218994 } },
  load_scalers_derived: { electrical: ['2035', '2040'] }, load_growth_pct_per_year: { electrical: 2 }, load_growth_base_year: 2030 }
const STATES = {
  'rate-only': { cfg: RATE },
  'typed-override': { cfg: { ...RATE, load_scalers_by_carrier: { electrical: { 2035: 1.3, 2040: 1.218994 } },
    load_scalers_derived: { electrical: ['2040'] } } },
  'validation-error': { cfg: RATE, invalid: true },
  'empty': { cfg: { load_scalers_by_carrier: {}, load_scalers_derived: {}, load_growth_pct_per_year: {}, load_growth_base_year: null } },
}
const TS = PERIODS.map(p => `${p}-01-01T00:00:00`)
const SNAP = { count: 3, snapshots: TS, periods: PERIODS, freq: 'h', ts_start: null, ts_end: null, can_sample_weeks: false,
  weightings: PERIODS.map((period, i) => ({ period, timestep: TS[i], objective: 1, generators: 1, stores: 1 })) }
const IP = { periods: PERIODS, weightings: PERIODS.map(period => ({ period, years: 5, objective: 5 })) }

async function ensureStub() {
  const up = await fetch('http://127.0.0.1:8000/api/health').then(r => r.ok).catch(() => false)
  if (up) return null
  const s = http.createServer((q, r) => { r.setHeader('content-type', 'application/json'); r.end(JSON.stringify({ status: 'ok', auth_enabled: false })) })
  await new Promise(res => s.listen(8000, '127.0.0.1', res))
  return s
}
async function ensureVite() {
  if (await fetch(BASE).then(r => r.ok).catch(() => false)) return null
  const p = spawn('npx', ['vite', '--host', '127.0.0.1', '--port', '5173', '--strictPort'], { cwd: frontend, stdio: 'ignore' })
  for (let i = 0; i < 90; i++) { if (await fetch(BASE).then(r => r.ok).catch(() => false)) return p; await new Promise(r => setTimeout(r, 1000)) }
  p.kill(); throw new Error('Vite did not come up on :5173')
}

await fs.mkdir(OUT, { recursive: true })
const problems = [], written = []
let stub = null, vite = null, browser = null
try {
  stub = await ensureStub()
  vite = await ensureVite()
  browser = await chromium.launch()
  for (const mode of ['guided', 'expert']) for (const [key, st] of Object.entries(STATES)) {
    if (process.env.ONLY && !key.startsWith(process.env.ONLY)) continue
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, reducedMotion: 'reduce' })
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
    const unmocked = new Set()
    await page.route(u => u.pathname.startsWith('/api/'), r => {
      const p = new URL(r.request().url()).pathname, m = r.request().method()
      const ok = json => r.fulfill({ json })
      if (p.startsWith('/api/chat/') || p.startsWith('/api/dictation/')) return r.abort()
      if (p === '/api/health') return ok({ status: 'ok', auth_enabled: false })
      if (p === '/api/simulation/solver_config') return ok(baseCfg(st.cfg))
      if (p === '/api/network/investment_periods') return ok(IP)
      if (p === '/api/network/snapshots') return ok(SNAP)
      if (p === '/api/network/loads') return ok([])
      if (p === '/api/simulation/status') return ok({ running: false, status: 'idle' })
      if (p === '/api/simulation/queue') return ok({ jobs: [], running: [], paused: false })
      if (p === '/api/projects/' || p === '/api/projects') return ok([{ name: PROJECT, bus_count: 2, snapshot_count: 3, has_results: false, created_at: '2026-01-01T00:00:00Z', is_root: true, parent: null }])
      if (/\/flow$/.test(p)) return ok({ sections: [], run_request: { optimisation: true, study: false, loadflow: false }, notes: [], deferred: false, deferred_reason: null })
      if (p.endsWith('/sites') && m === 'GET') return ok({ version: 1, asset_footprints: {}, sites: [] })
      if (p.endsWith('/context') || p.endsWith('/flow/scope')) return r.fulfill({ status: 404, json: { detail: 'none' } })
      if (m === 'GET' && p.startsWith('/api/results/')) return r.fulfill({ status: 204, body: '' })
      if (m === 'GET' && /\/api\/network\/[a-z_]+$/.test(p)) return ok([])
      unmocked.add(`${m} ${p}`)
      return ok(m === 'GET' && /(s|list|history|pending|workflows|profiles)$/.test(p) ? [] : {})
    })
    try {
      await page.goto(`${BASE}/`)
      await page.waitForLoadState('networkidle')
      const resume = page.getByRole('link', { name: /Resume project/ })
      if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
      await page.addStyleTag({ content: 'div[style*="z-index: 9999"] { display: none !important }' })
      await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('horizon') })
      const summary = page.getByRole('region', { name: 'Model horizon summary' })
      await summary.waitFor()
      await summary.getByRole('button', { name: /Economics/ }).click()
      await page.getByText('Period weightings').first().waitFor()
      await page.getByText('Advanced', { exact: true }).first().click()
      const growth = page.getByLabel('Load growth (% per year), Electrical')
      await growth.waitFor()
      if (st.invalid) {
        const heat = page.getByLabel('Load growth (% per year), Heat')
        await heat.fill('-150'); await heat.blur()
        await page.getByRole('alert').filter({ hasText: /above -100/ }).first().waitFor()
      }
      await page.waitForTimeout(600)
      const file = path.join(OUT, `${mode}-${key}.png`)
      // Crop to the box holding "Period weightings" (nearest ancestor that contains the table and the growth input).
      const box = page.getByText('Period weightings').first().locator('xpath=ancestor::*[.//table and .//input[contains(@aria-label,"Load growth")]][1]')
      const bb = await box.boundingBox().catch(() => null)
      if (bb && bb.height < 880) { await box.scrollIntoViewIfNeeded(); await box.screenshot({ path: file }) }
      else await page.screenshot({ path: file })
      written.push(path.basename(file))
    } catch (e) {
      problems.push(`${mode}/${key}: ${e.message.split('\n')[0]}`)
      await page.screenshot({ path: path.join(OUT, `failed-${mode}-${key}.png`) }).catch(() => {})
    }
    if (process.env.DEBUG) console.log(`${mode}/${key} unmocked:`, [...unmocked].join(' | '))
    await ctx.close()
  }
} finally { await browser?.close(); stub?.close(); vite?.kill() }
console.log(`wrote ${written.length}: ${OUT}\n${written.join(', ')}`)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
