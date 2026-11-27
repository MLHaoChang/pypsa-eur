// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** guided-results 04 follow-up shots (component harness, no API): the Expert LCOE card in the
 * out-of-date state, and Asset economics' system cost KPI under its ruled name, light and dark.
 * The Economics tab is Expert-only: `expertOnly: true` on its entry in src/pages/results/resultTabs.ts.
 * node scripts/guided-results-04-followup-shots.mjs [out-dir]   (default: .scratch/guided-results/shots)
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
const entry = `.gr04f-${process.pid}.tmp`
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
import {KPI} from './pages/results/shared'
import {HEADLINE_WORDS} from './pages/results/headlineWords'
import {LCOE_OUT_OF_DATE} from './pages/hubDesign/cards/ResultsHeadline'
import {figureBasis, figureName, figureTag} from './utils/figureWords'
import './index.css'
if (new URLSearchParams(location.search).get('theme') === 'dark') document.documentElement.dataset.theme = 'dark'
const Cap = ({children}: any) => <div className="text-[10px] font-semibold uppercase tracking-wide text-muted">{children}</div>
createRoot(document.getElementById('root')!).render(
  <div className="bg-panel text-text text-[11px] flex flex-col gap-3" style={{padding:16,maxWidth:640}} data-testid="harness">
    <Cap>Expert hub: the LCOE card after an edit that follows a run (state stale)</Cap>
    <div style={{width: 220}}><Kpi headline label={HEADLINE_WORDS.lcoe.label} value="" state="stale" reason={LCOE_OUT_OF_DATE} /></div>
    <Cap>Asset economics (Expert only): the system cost KPI in a four-column row</Cap>
    <div className="grid grid-cols-4 gap-2">
      <KPI label="Avg capture price" value="61.20 €/MWh" sub="Σ revenue / Σ energy delivered" />
      <KPI label={figureName('system_cost_per_mwh')} value="58.40 €/MWh" sub="(Σ fixed + vom + charge) / Σ energy"
        hint={figureTag('system_cost_per_mwh') + ': ' + figureBasis('system_cost_per_mwh')} />
      <KPI label="Net profit" value="1.2 M€" sub="revenue − cost" />
      <KPI label="Total revenue" value="9.8 M€" sub="Σ revenue" />
    </div>
  </div>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 0, fs: { allow: [root, path.resolve(root, '../backend/services/figure_words.json')] } } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  for (const theme of ['light', 'dark']) {
    const page = await browser.newPage({ viewport: { width: 680, height: 500 }, deviceScaleFactor: 2 })
    page.on('pageerror', e => { pageErrors.push(e.message) })
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html?theme=${theme}`)
    await page.getByTestId('harness').waitFor()
    const f = path.join(out, `gr04-followup-${theme}.png`)
    await page.getByTestId('harness').screenshot({ path: f }); console.log(f)
    await page.close()
  }
  if (pageErrors.length) throw new Error('page error: ' + pageErrors[0])
} finally {
  await browser?.close(); await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
