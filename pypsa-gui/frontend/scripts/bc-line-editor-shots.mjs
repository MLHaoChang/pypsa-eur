// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * Business case 09 screenshot + flow harness: the line editor on Results > Investment > Business case.
 * Every /api route is mocked in the browser (as reserve-shots.mjs), so no backend or sign-in is needed; the
 * shapes follow src/api/businessCase.ts. Invented numbers and generic names only.
 *
 * Needs Vite on :5173. Vite's auth gate asks :8000 for /api/health, so the script starts a stub there
 * (health only) unless something already answers.
 *
 * States (Expert at 1400 px; b and d also at 1000 px): a-empty, b-template-lines, c-refusal, d-not-established,
 * e-expanded, f-locked-before, f-locked-after. The Investment tab is `expertOnly` in Guided (Results.tsx TABS),
 * so Guided is skipped unless GUIDED=1 (then the tab is not found and the shot fails). `g-flow` (Expert) applies
 * the preset, types a price, saves, and asserts the PUT body and its If-Match header.
 *
 * Usage: node scripts/bc-line-editor-shots.mjs [outDir]      env: BASE, ONLY=<state prefix>, DEBUG=1, GUIDED=1
 */
import fs from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'business-case', 'shots', '09'))
const SCHEMAS = JSON.parse(await fs.readFile(path.join(frontend, 'src', 'test', 'fixtures', 'assetSchema.json'), 'utf8'))
const PROJECT = 'Line editor shots'

const LINES = () => [
  { name: 'Hydrogen sales', kind: 'revenue', category: 'product_sale', unit: 'kg', quantity_source: 'linked:served_h2.h2_offtake@1',
    price: null, escalation: 'contract', hint: { low: 3, high: 7, unit: 'EUR/kg', source: 'Public hydrogen auction results', year: 2024 } },
  { name: 'Staff', kind: 'opex', category: 'staff', unit: 'unit', quantity_source: 'typed', quantity: 12, price: 85000, escalation: 'opex',
    assumed: { price: { key: 'staff', value: 85000, unit: 'EUR/unit', source: 'Defaults pack', source_year: 2025, pack: 'defaults', status: 'assumed' } } },
  { name: 'Maintenance', kind: 'opex', category: 'maintenance', unit: 'share', quantity_source: 'share_of:capex', quantity: 0.02, escalation: 'opex' },
  { name: 'Electrolyser plant', kind: 'capex', category: 'facility', unit: 'lump', quantity_source: 'typed', quantity: 1, price: 42000000,
    life_years: 20, escalation: 'capex' },
]
const OPTIONS = (refusals = null) => ({
  template_id: 'eh_h2_hub', variants: { merchant: 'Merchant hub', contracted: 'Contracted offtake' }, offered_rows: [],
  categories: {
    revenue: ['product_sale', 'by_product', 'service_fee', 'lease', 'capacity_payment', 'tariff', 'cost_recovery', 'incentive', 'other'],
    opex: ['staff', 'maintenance', 'insurance', 'property_tax', 'feedstock', 'consumables', 'network', 'land', 'sga', 'other'],
    capex: ['facility', 'equipment', 'it_hardware', 'other'],
  },
  escalations: ['opex', 'fuel', 'tariff', 'ppa', 'export', 'capex', 'contract'],
  units_by_source: {
    typed: { revenue: ['MWh', 'MWh_H2', 'kg', 't', 'GPU_h', 'kW_month', 'kW_year', 'MW_year', 'unit', 'unit_month'],
      opex: ['MWh', 'MWh_H2', 'kg', 't', 'GPU_h', 'kW_month', 'kW_year', 'MW_year', 'unit', 'unit_month'],
      capex: ['MWh', 'MWh_H2', 'kg', 't', 'GPU_h', 'kW_month', 'kW_year', 'MW_year', 'unit', 'unit_month', 'MW', 'lump'] },
    share_of: { revenue: [], opex: ['share'], capex: [] },
    linked: { 'served_h2.h2_offtake': { revenue: ['MWh_H2', 'kg'], opex: [], capex: [] } },
  },
  catalogue_version: '1',
  outputs: [{ id: 'served_h2.h2_offtake', label: 'Hydrogen served to h2_offtake', units: ['MWh_H2', 'kg'], derived: false }],
  outputs_reason: null, horizon: [2030, 2034], base_year: 2030, refusals, refusals_reason: null,
})
const REFUSALS = [
  { code: 'business_case_refused', refusal: 'life_missing', field: 'lines[3].life_years', message: 'x' },
  { code: 'business_case_refused', refusal: 'unit_not_allowed', field: 'lines[1].unit', message: 'x' },
]
const REPORT = { status: 'done', report: { currency: 'EUR', basis: { id: 'project_on_its_own', label: 'the project on its own' },
  locked: [{ stream: 'bill', key: 'energy_import', dropped: [], superseded_by: [] },
    { stream: 'export', key: 'grid_export', dropped: [[2030, 1], [2031, 1]], superseded_by: ['Hydrogen sales'] }] }, staleness: { stale: false } }

// Business case 10: a run's report with a P&L and KPIs (a mixed-sign stream, a None tax).
const YEARS = [2030, 2031, 2032, 2033, 2034, 2035]
const ser = (...v) => YEARS.map((_, i) => v[i % v.length])
const RUN_REPORT = { ...REPORT.report,
  pnl: { years: YEARS,
    streams: { product_revenue: ser(4.2e6, 4.4e6, -0.3e6), bill: ser(-1.9e6), export: ser(0.25e6), bc_staff: ser(-0.6e6) },
    stream_class: { product_revenue: 'contract', bill: 'tariff', export: 'export', bc_staff: 'opex' },
    stream_source: { product_revenue: 'line:revenue', bill: 'energy_model', export: 'energy_model', bc_staff: 'line:opex' },
    rows: { revenue: ser(4.45e6, 4.65e6, 0.25e6), opex: ser(2.5e6), ebitda: ser(1.95e6, 2.15e6, -2.25e6), pass_through: ser(0),
      revenue_net_of_pass_through: ser(4.45e6, 4.65e6, 0.25e6), ebitda_net_of_pass_through: ser(1.95e6, 2.15e6, -2.25e6),
      ebitda_margin: ser(0.438, 0.462, null), terminal: ser(0), depreciation: ser(1.2e6), ebit: ser(0.75e6, 0.95e6, -3.45e6),
      tax: null, net_income: null, capex: ser(0), stage_capex: ser(0), replacement: ser(0), grant: ser(0), credits: ser(0),
      project_pre_tax: ser(1.95e6, 2.15e6, -2.25e6), project_post_tax: null, working_capital_change: null,
      project_after_working_capital: null, fees: ser(0), debt_service: ser(0), draws: ser(0), reserve_interest: ser(0),
      dsra_funding: ser(0), tax_equity: null, equity_pre_tax: null, equity_post_tax: null, equity_after_working_capital: null,
      debt_established: false },
    not_finite: [] },
  kpis: {
    project_npv: { value: null, status: 'not_established', reason: 'tax_not_established: corporate rate' },
    project_irr: { value: null, status: 'not_established', reason: 'tax_not_established: corporate rate' },
    equity_npv: { value: null, status: 'not_established', reason: 'debt_not_established' },
    equity_irr: { value: null, status: 'not_established', reason: 'debt_not_established' },
    payback_years: { value: null, status: 'not_established', reason: 'tax_not_established' },
    min_dscr: { value: null, status: 'n/a', reason: 'no_debt' }, avg_dscr: { value: null, status: 'n/a', reason: 'no_debt' },
    ebitda_margin_gross: { value: 0.31, status: 'ok', reason: null }, ebitda_margin_net: { value: 0.31, status: 'ok', reason: null },
    revenue_per_capacity_gross: { value: null, status: 'not_established', reason: 'capacity_not_stated', unit: 'EUR/MW-year' },
    revenue_per_capacity_net: { value: null, status: 'not_established', reason: 'capacity_not_stated', unit: 'EUR/MW-year' } },
  break_even: { status: null, price: 3.85, price_scale: 0.86, money_year: 2025 } }
const RAN = { status: 'done', report: RUN_REPORT, staleness: { stale: false, changed: [] } }
// The same report over `n` years (each series repeats), so the P&L scrolls at 1000 px.
const longer = (n) => {
  const ext = v => (Array.isArray(v) ? Array.from({ length: n }, (_, i) => v[i % v.length]) : v)
  const pnl = RUN_REPORT.pnl
  return { ...RAN, report: { ...RUN_REPORT, pnl: { ...pnl, years: Array.from({ length: n }, (_, i) => 2030 + i),
    streams: Object.fromEntries(Object.entries(pnl.streams).map(([k, v]) => [k, ext(v)])),
    rows: Object.fromEntries(Object.entries(pnl.rows).map(([k, v]) => [k, ext(v)])) } } }
}

// open: expand the named lines; wait: text to wait for; ran: GET business_case returns the report.
// study: what GET business_case returns instead (10); run: scroll to the run section.
const STATES = {
  'a-empty': { lines: [], status: 'not_set' },
  'b-template-lines': { lines: LINES(), narrow: true },
  'c-refusal': { lines: LINES(), refusals: REFUSALS },
  'd-not-established': { lines: LINES(), narrow: true },
  'h-scrolled': { lines: LINES(), narrow: true, only1000: true, scroll: true },
  'e-expanded': { lines: LINES(), expand: 'Electrolyser plant' },
  'f-locked-before': { lines: LINES(), locked: true },
  'f-locked-after': { lines: LINES(), locked: true, ran: true },
  'i-report': { lines: LINES(), narrow: true, run: true, study: RAN },
  'j-running': { lines: LINES(), run: true, study: { status: 'running', stage: 'run_engine', stages: ['rebuild_ic_case', 'read_outputs', 'build', 'run_engine', 'break_even', 'store'], report: RUN_REPORT } },
  'k-out-of-date': { lines: LINES(), run: true, study: { ...RAN, staleness: { stale: true, changed: ['investment_case'] } } },
  'l-not-run': { lines: LINES(), run: true },
  // Run off, its reason beside it (UX 5): unsaved lines, no lines, refused lines.
  'm-off-unsaved': { lines: LINES(), run: true, edit: true },
  'm-off-no-lines': { lines: [], status: 'not_set', run: true },
  'm-off-refused': { lines: LINES(), refusals: REFUSALS, run: true },
  // A 409 investment_case_missing from the run POST.
  'n-blocked': { lines: LINES(), run: true, post409: true, click: 'Run the business case', wait: 'bc-blocked' },
  // The P&L's sticky Line column, scrolled to its last year at 1000 px.
  'o-pnl-scrolled': { lines: LINES(), narrow: true, only1000: true, study: longer(20), pnlScroll: true },
}

async function ensureStub() {
  const up = await fetch('http://127.0.0.1:8000/api/health').then(r => r.ok).catch(() => false)
  if (up) return null
  const s = http.createServer((q, r) => { r.setHeader('content-type', 'application/json'); r.end(JSON.stringify({ status: 'ok', auth_enabled: false })) })
  await new Promise(res => s.listen(8000, '127.0.0.1', res))
  return s
}

async function newPage(browser, mode, width, st, probe) {
  const ctx = await browser.newContext({ viewport: { width, height: 1000 }, deviceScaleFactor: 1, reducedMotion: 'reduce' })
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
  page.on('pageerror', e => probe.problems.push(`${mode}/${probe.key}: page error: ${(e.stack || e.message).split('\n').slice(0, 4).join(' / ')}`))
  const unmocked = new Set()
  await page.route(u => u.pathname.startsWith('/api/'), r => {
    const req = r.request(), p = new URL(req.url()).pathname, m = req.method()
    const ok = json => r.fulfill({ json })
    const inputs = digest => ({ business_case: st.lines.length || st.saved ? { inputs: { lines: st.saved ?? st.lines } } : null, digest,
      status: st.status ?? 'ok' })
    if (p.startsWith('/api/chat/') || p.startsWith('/api/dictation/')) return r.abort()
    if (p === '/api/health') return ok({ status: 'ok', auth_enabled: false })
    if (p === '/api/results/business_case/inputs') {
      if (m === 'PUT') { probe.puts.push({ body: req.postDataJSON(), ifMatch: req.headers()['if-match'] }); st.saved = req.postDataJSON().business_case.inputs.lines; return ok(inputs('d2')) }
      return ok(inputs('d1'))
    }
    if (p === '/api/results/business_case/options') return ok(OPTIONS(st.refusals ?? null))
    if (p === '/api/results/business_case/preset') return ok({ template_id: 'eh_h2_hub', variants: { merchant: 'Merchant hub', contracted: 'Contracted offtake' },
      business_case: { inputs: { preset: 'eh_h2_hub.merchant@1', lines: LINES() } } })
    if (p === '/api/results/business_case' && m === 'POST' && st.post409) return r.fulfill({ status: 409, json: { detail: { code: 'investment_case_missing', reason: 'no_report' } } })
    if (p === '/api/results/business_case') return st.study ? ok(st.study) : st.ran ? ok(REPORT) : r.fulfill({ status: 204, body: '' })
    if (p === '/api/simulation/status') return ok({ running: false, status: 'completed', condition: 'optimal', objective: 1234.5, solve_time: 2.5, dispatch: 'fresh' })
    if (p === '/api/simulation/queue') return ok({ jobs: [], running: [], paused: false })
    if (p === '/api/network/vintage_results') return ok({ results: {} })
    if (p === '/api/network/investment_periods') return ok({ periods: [], weightings: [] })
    if (p === '/api/network/snapshots') return ok({ count: 1, snapshots: ['2030-01-01T00:00:00'], weightings: [], ts_start: '2030-01-01T00:00:00', ts_end: '2030-01-01T00:00:00', can_sample_weeks: false })
    if (p.endsWith('/flow/scope')) return r.fulfill({ status: 404, json: { detail: 'none' } })
    if (/\/flow$/.test(p)) return ok({ sections: [], run_request: { optimisation: true, study: false, loadflow: false }, notes: [], deferred: false, deferred_reason: null })
    const sch = p.match(/^\/api\/network\/asset_schema\/([^/]+)$/)
    if (sch) return SCHEMAS[sch[1]] ? ok(SCHEMAS[sch[1]]) : r.fulfill({ status: 404, json: { detail: 'none' } })
    if (p === '/api/projects/' || p === '/api/projects') return ok([{ name: PROJECT, bus_count: 2, snapshot_count: 1, has_results: true, created_at: '2026-01-01T00:00:00Z', is_root: true, parent: null }])
    if (p.endsWith('/context')) return r.fulfill({ status: 404, json: { detail: 'none' } })
    if (m === 'GET' && p.startsWith('/api/results/')) return r.fulfill({ status: 204, body: '' })
    if (m === 'GET' && /\/api\/network\/[a-z_]+$/.test(p)) return ok([])
    unmocked.add(`${m} ${p}`)
    return ok(m === 'GET' && /(s|list|history|pending|workflows|profiles)$/.test(p) ? [] : {})
  })
  probe.unmocked = unmocked
  return { ctx, page }
}

async function openEditor(page) {
  await page.goto(`${BASE}/`)
  await page.waitForLoadState('networkidle')
  const resume = page.getByRole('link', { name: /Resume project/ })
  if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
  await page.addStyleTag({ content: 'div[style*="z-index: 9999"] { display: none !important }' })
  await page.evaluate(async () => { const { showResultsTab } = await import('/src/layout/flowNavigation.ts'); showResultsTab('investment', { section: 'business_case' }) })
  await page.getByTestId('bc-toolbar').waitFor()
  await page.waitForTimeout(800)
}

await fs.mkdir(OUT, { recursive: true })
const stub = await ensureStub()
const browser = await chromium.launch()
const problems = [], written = []
const shootTo = async (page, name) => { await page.screenshot({ path: path.join(OUT, `${name}.png`) }); written.push(`${name}.png`) }
try {
  const jobs = []
  for (const [key, st] of Object.entries(STATES)) {
    if (!st.only1000) jobs.push([key, 'expert', 1400, st])
    if (st.narrow) jobs.push([key, 'expert', 1000, st])
    if (process.env.GUIDED) jobs.push([key, 'guided', 1400, st])
  }
  for (const [key, mode, width, st0] of jobs) {
    if (process.env.ONLY && !key.startsWith(process.env.ONLY)) continue
    const st = { ...st0 }, probe = { key, problems, puts: [] }
    const { ctx, page } = await newPage(browser, mode, width, st, probe)
    try {
      await openEditor(page)
      if (st.expand) { await page.getByRole('button', { name: `Show details of ${st.expand}` }).click(); await page.waitForTimeout(600) }
      if (st.scroll) {
        await page.evaluate(() => {
          let el = document.querySelector('[aria-label^="Price, "]')
          while (el && !(el.scrollWidth > el.clientWidth + 1 && /(auto|scroll)/.test(getComputedStyle(el).overflowX))) el = el.parentElement
          if (el) el.scrollLeft = el.scrollWidth
        })
        await page.waitForTimeout(400)
      }
      if (st.locked) await page.getByTestId('bc-locked').scrollIntoViewIfNeeded()
      if (st.ran) await page.getByTestId('bc-locked-row').first().waitFor()
      if (st.edit) { const price = page.getByLabel('Price, Hydrogen sales'); await price.fill('4.5'); await price.blur() }
      if (st.pnlScroll) {
        await page.evaluate(() => {
          const el = document.querySelector('[data-testid="bc-pnl"]')
          el?.scrollIntoView({ block: 'start' })
          if (el) el.scrollLeft = el.scrollWidth
        })
        await page.waitForTimeout(400)
      }
      if (st.run) { await page.getByTestId('bc-run').scrollIntoViewIfNeeded(); await page.evaluate(() => document.querySelector('[data-testid="bc-run"]')?.scrollIntoView({ block: 'start' })); await page.waitForTimeout(400) }
      if (st.click) { await page.getByRole('button', { name: st.click }).click(); await page.getByTestId(st.wait).waitFor() }
      // The floating "Back to the canvas" pill sits over the lower page: scroll the
      // report's last row clear of it (UX nit on BC 10 k).
      if (st.study && !st.pnlScroll) {
        await page.evaluate(() => {
          const pill = [...document.querySelectorAll('button')].find(b => /Back to the canvas/.test(b.textContent || ''))
          const pnl = document.querySelector('[data-testid="bc-pnl"]')
          if (!pill || !pnl) return
          const top = pill.getBoundingClientRect().top, bottom = pnl.getBoundingClientRect().bottom
          if (bottom > top - 8) {
            let el = pnl.parentElement
            while (el && !(el.scrollHeight > el.clientHeight + 1 && /(auto|scroll)/.test(getComputedStyle(el).overflowY))) el = el.parentElement
            if (el) el.scrollTop += bottom - top + 16
          }
        })
        await page.waitForTimeout(300)
      }
      await shootTo(page, `${key}-${mode}${width === 1000 && !st.only1000 ? '-1000' : ''}`)
    } catch (e) {
      problems.push(`${mode}/${key}/${width}: ${e.message.split('\n')[0]}`)
      await page.screenshot({ path: path.join(OUT, `failed-${mode}-${key}-${width}.png`) }).catch(() => {})
    }
    if (process.env.DEBUG) console.log(`${mode}/${key} unmocked:`, [...probe.unmocked].join(' | '))
    await ctx.close()
  }

  // g-flow: Apply preset, type a price, save; assert the PUT.
  if (!process.env.ONLY || 'g-flow'.startsWith(process.env.ONLY)) {
    const st = { lines: [], status: 'not_set' }, probe = { key: 'g-flow', problems, puts: [] }
    const { ctx, page } = await newPage(browser, 'expert', 1400, st, probe)
    try {
      await openEditor(page)
      await page.getByRole('button', { name: 'Apply preset' }).click()
      const price = page.getByLabel('Price, Hydrogen sales')
      await price.fill('4.5'); await price.blur()
      await page.getByRole('button', { name: 'Save the lines' }).click()
      await page.waitForTimeout(1000)
      if (probe.puts.length !== 1) throw new Error(`flow: expected 1 PUT, saw ${probe.puts.length}`)
      const { body, ifMatch } = probe.puts[0]
      const got = body?.business_case?.inputs?.lines?.[0]?.price
      if (got !== 4.5) throw new Error(`flow: lines[0].price is ${JSON.stringify(got)}, wanted 4.5`)
      if (ifMatch !== 'd1') throw new Error(`flow: If-Match is ${JSON.stringify(ifMatch)}, wanted d1`)
      console.log('g-flow: PUT body lines[0].price === 4.5 and If-Match === d1: ok')
      await shootTo(page, 'g-flow-expert')
    } catch (e) {
      problems.push(`expert/g-flow: ${e.message.split('\n')[0]}`)
      await page.screenshot({ path: path.join(OUT, 'failed-expert-g-flow.png') }).catch(() => {})
    }
    await ctx.close()
  }
} finally { await browser.close(); stub?.close() }
console.log(`wrote ${written.length}: ${OUT}\n${written.join(', ')}`)
if (problems.length) { console.error(problems.join('\n')); process.exit(1) }
