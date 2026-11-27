// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** te-24 LCOE caveat shots (LCOE-CAVEAT-1): the Guided Results card and the Expert headline
 * strip with the caveat under an LCOE above EUR 300/MWh (hospital, backup cause), and the strip
 * with the neutral line (above the bound, no backup cause), light and dark
 * (component harness; the API objects are stubbed, any other /api call answers null).
 * node scripts/te24-lcoe-caveat-shots.mjs [out-dir]   (default: .scratch/template-e2e/evidence/24)
 */
import fs from 'node:fs/promises'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
const { chromium } = createRequire(import.meta.url)('playwright')
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const entry = `.te24lc-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = path.resolve(process.argv[2] || path.join(root, '..', '..', '.scratch', 'template-e2e', 'evidence', '24'))
let server, browser
const pageErrors = []
try {
  await fs.mkdir(out, { recursive: true })
  await fs.writeFile(html, `<!doctype html><html><body class="bg-bg text-text"><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import './index.css'
import {resultsApi} from './api/simulation'
import {REPORT, SITE_TEMPLATE, review} from './pages/hubDesign/testFixtures'
import {ResultsCard} from './pages/hubDesign/cards/ResultsCard'
import HeadlineStrip from './pages/results/HeadlineStrip'
import {ChartSourceProvider} from './components/charts/chartTheme'
import {useUIStore} from './store/uiStore'
import {HUB_DESIGN_INITIAL, useHubDesignStore} from './pages/hubDesign/hubDesignStore'
const q = new URLSearchParams(location.search)
if (q.get('theme') === 'dark') document.documentElement.dataset.theme = 'dark'
useUIStore.setState({ currentProject: 'Hospital', activeSlidePanel: 'hubDesign' } as any)
useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Hospital', ready: true, step: 'results' } as any)
const r: any = resultsApi
r.getEhStudy = async () => ({ status: 'done', archetype: 'weak_flexible', report: REPORT, edited_since_study: false, finished_at: '2026-11-27T10:00:00Z' })
r.getEhReview = async () => review()
r.getEhReferenceDesign = async () => REPORT
r.getEhTemplate = async () => SITE_TEMPLATE
const CAUSE = 'Above \u20ac\u00a0300/MWh, high for a hospital: the backup kept for the critical load is costed but rarely runs.'
const NEUTRAL = 'Above \u20ac\u00a0300/MWh, high for a hospital.'
const lcoe = { id: 'lcoe', value: 413.2, unit: 'EUR/MWh', state: 'ready', reason: null, formula_id: 'investment-lcoe-real/1',
  detail: null, delta: null, trend: null, money_year: 2025, basis: 'investment case \u00b7 real basis, 2025 EUR',
  caveat: q.get('case') === 'neutral' ? NEUTRAL : CAUSE }
r.getHeadlineKpis = async () => ({ kind: 'energy_hub', cards: [lcoe] })
r.getHeadlineComparison = async () => null
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}>
    <div data-testid="harness" className="p-4 w-[640px] bg-bg">{q.get('view') === 'guided' ? <ResultsCard /> :
      <ChartSourceProvider value={{ line: 'Latest solve \u00b7 solved', stale: false }}><HeadlineStrip /></ChartSourceProvider>}</div>
  </QueryClientProvider>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0, fs: { allow: [root, path.resolve(root, '../backend/services/figure_words.json')] } } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  for (const [view, kase] of [['guided', 'cause'], ['expert', 'cause'], ['expert', 'neutral']]) for (const theme of ['light', 'dark']) {
    const page = await browser.newPage({ viewport: { width: 680, height: 1200 }, deviceScaleFactor: 2 })
    page.on('pageerror', e => { pageErrors.push(e.message) })
    await page.route(u => u.pathname.startsWith('/api/'), r => r.fulfill({ status: 200, contentType: 'application/json', body: 'null' }))
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html?theme=${theme}&view=${view}&case=${kase}`)
    await page.getByTestId(view === 'guided' ? 'hub-results-lcoe-caveat' : 'headline-lcoe-caveat').waitFor()
    await page.waitForTimeout(500)
    const f = path.join(out, `te24-lcoe-caveat-${view}-${kase}-${theme}.png`)
    await page.getByTestId('harness').screenshot({ path: f }); console.log(f)
    await page.close()
  }
  if (pageErrors.length) throw new Error('page error: ' + pageErrors[0])
} finally {
  await browser?.close(); await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
