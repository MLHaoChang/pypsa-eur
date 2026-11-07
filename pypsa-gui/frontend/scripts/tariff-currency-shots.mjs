// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/**
 * GridSpine Library 02 screenshot harness: the tariff builder's currency input, Guided and Expert.
 * Every /api route is mocked in the browser (as reserve-shots.mjs); invented numbers only.
 * Needs Vite on :5173 (`npx vite --host 127.0.0.1`); a health stub is started on :8000 unless one answers.
 *
 * States: `a-nl` (jurisdiction NL, currency blank, placeholder EUR), `b-generic` (the muted "State a currency"
 * note), `c-usd` (USD typed, NL), `d-error` (Save to Library refused 422 tariff_currency_required on "generic").
 * Files: <state>-<guided|expert>.png.
 *
 * Usage: node scripts/tariff-currency-shots.mjs [outDir]      env: BASE, ONLY=<state prefix>, DEBUG=1
 */
import fs from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const { chromium } = createRequire(import.meta.url)('playwright')
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASE = process.env.BASE || 'http://127.0.0.1:5173'
const OUT = path.resolve(process.argv[2] || path.join(frontend, '..', '..', '.scratch', 'gridspine-library-eu', 'shots', '02'))
const PROJECT = 'Tariff shots'

const TARIFF = { id: 'nl_demo', name: 'Demo tariff', jurisdiction: 'NL', valid_from: '2026-01-01',
  items: [{ id: 'energy', kind: 'energy', unit: 'EUR/MWh', periods: [{ name: 'all', rate: 95 }] }] }
const ERR_MSG = '“generic” names no country, so the tariff\'s currency is unknown. Use the country\'s two-letter code, such as NL, GB or US, or state the currency.'

const STATES = {
  'a-nl': async () => {},
  'b-generic': async ({ juris }) => { await juris.fill('generic') },
  'c-usd': async ({ cur }) => { await cur.fill('USD') },
  'd-error': async ({ juris, page }) => {
    await juris.fill('generic')
    await page.getByRole('button', { name: 'Save to Library' }).click()
    await page.getByText(/Currency: /).waitFor()
  },
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
  for (const mode of ['guided', 'expert']) for (const [key, act] of Object.entries(STATES)) {
    if (process.env.ONLY && !key.startsWith(process.env.ONLY)) continue
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 1, reducedMotion: 'reduce' })
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
    page.on('pageerror', e => problems.push(`${mode}/${key}: page error: ${(e.stack || e.message).split('\n').slice(0, 3).join(' / ')}`))
    if (process.env.DEBUG) page.on('console', m => { if (m.type() === 'error') console.log(`console: ${m.text().slice(0, 700)}`) })
    const unmocked = new Set()
    await page.route(u => u.pathname.startsWith('/api/'), r => {
      const p = new URL(r.request().url()).pathname, m = r.request().method()
      const ok = json => r.fulfill({ json })
      if (p.startsWith('/api/chat/') || p.startsWith('/api/dictation/')) return r.abort()
      if (p === '/api/health') return ok({ status: 'ok', auth_enabled: false })
      if (p === '/api/simulation/solver_config') return ok({ commercial: { import_tariff: TARIFF } })
      if (p === '/api/simulation/commercial/poc') return ok({ poc_link: null, open: false })
      if (m === 'PUT' && p.startsWith('/api/library/items/tariff/'))
        return r.fulfill({ status: 422, json: { detail: { code: 'tariff_currency_required', message: 'The tariff\'s currency is unknown.',
          errors: [{ loc: ['currency'], msg: ERR_MSG }] } } })
      if (p === '/api/network/meta') return ok({ name: 'Tariff shots', snapshot_count: 24, bus_count: 2, loaded_project: PROJECT })
      if (p.endsWith('/flow/reliability_goal')) return r.fulfill({ status: 404, json: { detail: 'none' } })
      if (p.endsWith('/sites') && m === 'GET') return ok({ version: 1, asset_footprints: {}, sites: [] })
      if (p.includes('/value_flows') || p === '/api/simulation/finance') return r.fulfill({ status: 404, json: { detail: 'none' } })
      if (p === '/api/network/snapshots') return ok({ count: 24, snapshots: [], weightings: [], ts_start: null, ts_end: null, can_sample_weeks: false })
      if (m === 'GET' && /^\/api\/network\/[a-z_]+$/.test(p)) return ok([])
      if (p === '/api/simulation/status') return ok({ running: false, status: 'idle' })
      if (p === '/api/simulation/queue') return ok({ jobs: [], running: [], paused: false })
      if (p === '/api/projects/' || p === '/api/projects') return ok([{ name: PROJECT, bus_count: 2, snapshot_count: 24, has_results: false, created_at: '2026-01-01T00:00:00Z', is_root: true, parent: null }])
      if (p.endsWith('/flow/scope') || p.endsWith('/context')) return r.fulfill({ status: 404, json: { detail: 'none' } })
      if (/\/flow$/.test(p)) return ok({ sections: [], run_request: { optimisation: true, study: false, loadflow: false }, notes: [], deferred: false, deferred_reason: null })
      if (m === 'GET' && p.startsWith('/api/results/')) return r.fulfill({ status: 204, body: '' })
      unmocked.add(`${m} ${p}`)
      return ok(m === 'GET' && /(s|list|history|pending|workflows|profiles)$/.test(p) ? [] : {})
    })
    try {
      await page.goto(`${BASE}/`)
      await page.waitForLoadState('networkidle')
      const resume = page.getByRole('link', { name: /Resume project/ })
      if (await resume.count()) { await resume.first().click(); await page.waitForURL(u => !u.pathname.startsWith('/projects')); await page.waitForLoadState('networkidle') }
      await page.addStyleTag({ content: 'div[style*="z-index: 9999"] { display: none !important }' })
      if (mode === 'guided') {
        await page.evaluate(async () => { const { openSectionInGuided } = await import('/src/layout/flowNavigation.ts'); openSectionInGuided('commercial') })
      } else {
        await page.evaluate(async () => { const { useUIStore } = await import('/src/store/uiStore.ts'); useUIStore.getState().setSlidePanel('economics') })
        await page.getByRole('tab', { name: /Tariffs/ }).click()
      }
      const builder = page.getByTestId('tariff-builder')
      await builder.waitFor()
      const juris = builder.getByLabel(/jurisdiction/i), cur = builder.getByLabel(/currency/i)
      await act({ juris, cur, page })
      await page.waitForTimeout(500)
      const file = path.join(OUT, `${key}-${mode}.png`)
      await builder.scrollIntoViewIfNeeded()
      await builder.screenshot({ path: file })
      written.push(path.basename(file))
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
