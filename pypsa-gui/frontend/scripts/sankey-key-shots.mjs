// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** Sankey process-node key, light and dark (EnergyFlowsTab harness, route-mocked API).
 * node scripts/sankey-key-shots.mjs [out-dir]   (default: .scratch/site-commercial-ux/shots)
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
const entry = `.sankey-key-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = path.resolve(process.argv[2] || path.join(root, '..', '..', '.scratch', 'site-commercial-ux', 'shots'))
const rows = [
  ['wind', 'Direct', 'Process nh3', 60], ['solar', 'Direct', 'Process nh3', 20], ['solar', 'Direct', 'load', 80],
  ['Grid import', 'Direct', 'load', 40], ['Grid import', 'Direct', 'Losses', 5],
].map(([source, via, destination, mwh]) => ({ source, via, destination, mwh }))
const PAYLOAD = {
  status: 'ok', unit: 'MWh', period: null, periods: [null],
  window: { from: 0, to: 167, snapshots: 168, start: '2030-01-01 00:00:00', end: '2030-01-07 23:00:00', first: '2030-01-01 00:00:00', last: '2030-01-07 23:00:00' },
  rows, series: { index: ['2030-01-01 00:00:00', '2030-01-01 01:00:00'], bucket_snapshots: 1, supply: { solar: [1, 2] }, demand: { load: [4, 6] } },
  conservation: { status: 'ok', checks: [], generation_mwh: 205, destinations_mwh: 205, storage_delta_mwh: 0, stock_mwh: 0, residual_mwh: 0, tolerance_mwh: 1 },
  unserved_mwh: null, flags: [],
}
let server, browser
const pageErrors = []
try {
  await fs.mkdir(out, { recursive: true })
  await fs.writeFile(html, `<!doctype html><html><body class="bg-bg text-text"><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import EnergyFlowsTab from './pages/results/EnergyFlowsTab'
import {useUIStore} from './store/uiStore'
import './index.css'
if (new URLSearchParams(location.search).get('theme') === 'dark') document.documentElement.dataset.theme = 'dark'
useUIStore.setState({ currentProject: 'Demo' })
createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={new QueryClient()}>
    <div className="bg-panel text-text text-[11px]" style={{padding:16,width:760}} data-testid="harness"><EnergyFlowsTab sankeyWidth={700}/></div>
  </QueryClientProvider>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()], server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  for (const theme of ['light', 'dark']) {
    const page = await browser.newPage({ viewport: { width: 800, height: 700 }, deviceScaleFactor: 2 })
    page.on('pageerror', e => { pageErrors.push(e.message) })
    await page.route('**/results/energy_flows*', r => r.fulfill({ json: PAYLOAD }))
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html?theme=${theme}`)
    await page.getByTestId('harness').waitFor()
    const f = path.join(out, `sankey-key-${theme}.png`)
    await page.getByTestId('harness').screenshot({ path: f }); console.log(f)
    await page.close()
  }
  if (pageErrors.length) throw new Error('page error: ' + pageErrors[0])
} finally {
  await browser?.close(); await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
