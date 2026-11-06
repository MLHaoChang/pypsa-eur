// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT
/** UIP-01 item 5 shots, Guided and Expert: the dips and restart card's Basis lines (component harness, no API).
 * node scripts/ui-polish-basis-shots.mjs [out-dir]   (default: .scratch/ui-polish/shots)
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
const entry = `.ui-polish-basis-${process.pid}.tmp`
const html = path.join(root, `${entry}.html`)
const source = path.join(root, 'src', `${entry}.tsx`)
const out = path.resolve(process.argv[2] || path.join(root, '..', '..', '.scratch', 'ui-polish', 'shots'))
let server, browser
const pageErrors = []
try {
  await fs.mkdir(out, { recursive: true })
  await fs.writeFile(html, `<!doctype html><html><body><div id="root"></div><script type="module" src="/src/${entry}.tsx"></script></body></html>`)
  await fs.writeFile(source, `
import React from 'react'
import {createRoot} from 'react-dom/client'
import CampusDipsRestartSection from './pages/CampusDipsRestartSection'
import {useUIStore} from './store/uiStore'
import './index.css'
const mode = new URLSearchParams(location.search).get('mode') === 'expert' ? 'expert' : 'guided'
useUIStore.setState({ uiMode: mode } as any)
const NONE = { ride_through_curve: null, restart_time_h: null, motor_kw: null, start_method: null, i_start_pu: null }
const dips: any = {
  inputs: { mill: { ride_through_curve: '20:0.5', restart_time_h: 4, motor_kw: 1500, start_method: 'vfd', i_start_pu: 1.2 }, pump: NONE },
  dip_label: "screening estimate; the binding answer is the operator's dip statistics and the units' tested curves",
  restart_label: 'event model, not dynamics; the binding answer is an RMS study of the restart',
  basis: ['demo dip statistics'],
  dip_trips: [{ level: 'unit', unit: 'mill', trips_per_year: 20.45, restart_time_h: 4, rate_tph: 4, mass_pull: 1, lost_t_per_trip: 16, lost_t_per_year: 327.2, verdict: 'known', dip_stats_source: 'demo', note: null }],
  restart: [], out_of_date: [],
}
createRoot(document.getElementById('root')!).render(<div style={{padding:16,maxWidth:760}}><CampusDipsRestartSection dips={dips}/></div>)
`)
  server = await createServer({ configFile: false, root, appType: 'mpa', plugins: [react(), tailwindcss()], server: { host: '127.0.0.1', port: 0 } })
  await server.listen()
  browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  for (const mode of ['guided', 'expert']) {
    const page = await browser.newPage({ viewport: { width: 820, height: 520 }, deviceScaleFactor: 2 })
    page.on('pageerror', e => { pageErrors.push(e.message) })
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${entry}.html?mode=${mode}`)
    await page.getByTestId('dips-basis-dips').waitFor()
    const f = path.join(out, `uip01-${mode}-4-after-dips-restart-basis.png`)
    await page.screenshot({ path: f }); console.log(f)
    await page.close()
  }
  if (pageErrors.length) throw new Error('page error: ' + pageErrors[0])
} finally {
  await browser?.close(); await server?.close()
  await Promise.all([fs.rm(html, { force: true }), fs.rm(source, { force: true })])
}
