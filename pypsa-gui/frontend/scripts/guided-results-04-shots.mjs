// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** guided-results 04 slice 2 shots: the Guided NPV (after and before tax), shortfall and LCOE
 * tiles under the one source's names, and Expert's Investment names, light and dark
 * (component harness, no API).
 * node scripts/guided-results-04-shots.mjs [out-dir]   (default: .scratch/guided-results/shots)
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
const entry = `.gr04-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = path.resolve(process.argv[2] || path.join(root, '..', '..', '.scratch', 'guided-results', 'shots'))
let server, browser
const pageErrors = []
try {
  await fs.mkdir(out, { recursive: true })
  await fs.writeFile(html, `<!doctype html><html><body class="bg-bg text-text"><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import Kpi from './components/Kpi'
import {chapterTiles, tileHint} from './pages/hubDesign/chapters'
import {HEADLINES} from './pages/results/investment/financeModel'
import {HEADLINE_WORDS, headlineFigure} from './pages/results/headlineWords'
import {figureBasis, figureHint, figureName} from './utils/figureWords'
import {PortfolioSection} from './pages/results/McPanel'
import './index.css'
if (new URLSearchParams(location.search).get('theme') === 'dark') document.documentElement.dataset.theme = 'dark'
const NONE: any = { investment: { study: null, report: null }, grid: { campus: null, notHub: true },
  reliability: { hasResults: false, running: false, lole: null, target: null, verdict: null }, operation: { status: null } }
const report = (npv: number | null, pre: number) => ({ npv_at_wacc: npv, sections: { project: { status: 'ok', payload: { project_pre_tax_npv: pre } } } })
const tile = (inputs: any, id: string) => chapterTiles({ ...NONE, ...inputs }).find(t => t.id === id)!
const done = { status: 'done' }
const tiles = [
  tile({ investment: { study: done, report: report(-1.2e6, -1e6) } }, 'investment'),
  tile({ investment: { study: done, report: report(null, -11.01e6) } }, 'investment'),
  tile({ reliability: { hasResults: true, running: false, lole: 1.2, target: 3, verdict: 'pass', horizonYears: 1 } }, 'reliability'),
]
const card: any = { id: 'lcoe', value: 77.5, unit: 'EUR/MWh', state: 'ready', reason: null, formula_id: null, detail: null, basis: 'investment case · real basis, 2024 EUR' }
const lcoe = headlineFigure(card)
const PORTFOLIO: any = { status: 'ok', reason: null,
  population: { members: [{ kind: 'vre', name: 'Wind-North', capacity_mw: 250 }], unbuilt: [], n_vre: 1, n_generator: 0 },
  margin_available: true, load_basis: 'lp',
  periods: [{ period: '2030', nameplate_mw: 280, elcc_mw: 130.25, elcc_share: 0.4652, status: 'ok', reason: null,
    baseline_lole_h: 4.5, baseline_lole_ci: [4.1, 4.9], credit_gross_mw: 171.5, credit_net_mw: 88.25 }] }
const Cap = ({children}: any) => <div className="text-[10px] font-semibold uppercase tracking-wide text-muted">{children}</div>
createRoot(document.getElementById('root')!).render(
  <div className="bg-panel text-text text-[11px] flex flex-col gap-3" style={{padding:16,maxWidth:640}} data-testid="harness">
    <Cap>Guided: the whole picture (half-width tiles)</Cap>
    <div className="grid grid-cols-2 gap-2">
      {tiles.map((t, i) => <Kpi key={i} headline lead={t.lead} label={t.label} value={t.value} unit={t.unit} basis={t.basis} state={t.state} hint={tileHint(t)} />)}
    </div>
    <Cap>Expert headline strip: the LCOE card</Cap>
    <div style={{width: 220}}><Kpi label={HEADLINE_WORDS.lcoe.label} value={lcoe.value} basis={lcoe.basis} /></div>
    <Cap>Tooltip text (title=) on the shortfall tile and McPanel headers</Cap>
    <div className="text-muted">{tileHint(tiles[2])}</div>
    <Cap>Adequacy (McPanel): the metric label and the ELCC portfolio table</Cap>
    <div className="text-[10px] text-muted">{figureName('expected_shortfall')} <span className="font-mono text-text">1.2 h/yr</span></div>
    <PortfolioSection block={PORTFOLIO} unit="h/yr" />
    <Cap>Investment tab: headline names</Cap>
    <div className="text-muted">{HEADLINES.filter(h => ['npv_at_wacc', 'lcoe', 'lcoe_real'].includes(h.id)).map(h => h.label).join(' · ')}</div>
    <Cap>Compare: the per-carrier row (hover: {figureBasis('carrier_lcoe')})</Cap>
    <table className="text-[11px]"><tbody><tr><td className="pr-3 text-muted">{figureName('carrier_lcoe')}</td><td className="pr-3 font-mono">62.10 €/MWh</td><td className="font-mono">58.40 €/MWh</td></tr></tbody></table>
  </div>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0, fs: { allow: [root, path.resolve(root, '../backend/services/figure_words.json')] } } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  for (const theme of ['light', 'dark']) {
    const page = await browser.newPage({ viewport: { width: 680, height: 900 }, deviceScaleFactor: 2 })
    page.on('pageerror', e => { pageErrors.push(e.message) })
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html?theme=${theme}`)
    await page.getByTestId('harness').waitFor()
    const f = path.join(out, `gr04-slice2-figure-names-${theme}.png`)
    await page.getByTestId('harness').screenshot({ path: f }); console.log(f)
    await page.close()
  }
  if (pageErrors.length) throw new Error('page error: ' + pageErrors[0])
} finally {
  await browser?.close(); await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
